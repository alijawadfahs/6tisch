#!/usr/bin/python
'''
\brief Collects and logs statistics about the ongoing simulation.

\author Thomas Watteyne <watteyne@eecs.berkeley.edu>
\author Kazushi Muraoka <k-muraoka@eecs.berkeley.edu>
\author Nicola Accettura <nicola.accettura@eecs.berkeley.edu>
\author Xavier Vilajosana <xvilajosana@eecs.berkeley.edu>
'''

#============================ logging =========================================

import logging
class NullHandler(logging.Handler):
    def emit(self, record):
        pass
log = logging.getLogger('SimStats')
log.setLevel(logging.ERROR)
log.addHandler(NullHandler())

#============================ imports =========================================

import SimEngine
import SimSettings
import Propagation

#============================ defines =========================================

#============================ body ============================================

class SimStats(object):

    #===== start singleton
    _instance      = None
    _init          = False

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(SimStats,cls).__new__(cls, *args, **kwargs)
        return cls._instance
    #===== end singleton

    def __init__(self,runNum,numRuns):

        #===== start singleton
        if self._init:
            return
        self._init = True
        #===== end singleton

        # store params
        self.runNum                         = runNum
        self.numRuns                        = numRuns
        #recieving the number of runs 
        # local variables
        self.engine                         = SimEngine.SimEngine()
        self.settings                       = SimSettings.SimSettings()
        self.propagation                    = Propagation.Propagation()

        # stats
        self.stats                          = {}
        self.columnNames                    = []
        self.latencyPerRank                 = {}
        
        # start file
        if self.runNum==0:
            self._fileWriteHeader()

        # schedule actions
        self.engine.scheduleAtStart(
            cb          = self._actionStart,
        )
        self.engine.scheduleAtAsn(
            asn         = self.engine.getAsn()+self.settings.slotframeLength-1,
            cb          = self._actionEndCycle,
            args        = None,
            uniqueTag   = (None,'_actionEndCycle'),
            priority    = 10,
        )
        self.engine.scheduleAtEnd(
            cb          = self._actionEnd,
        )

    def destroy(self):
        # destroy my own instance
        self._instance                      = None
        self._init                          = False

    #======================== private =========================================

    def _actionStart(self):
        '''Called once at beginning of the simulation.'''
        pass

    def _actionEndCycle(self, args=None):
        '''Called at each end of cyle.'''

        cycle = int(self.engine.getAsn()/self.settings.slotframeLength)

        if self.settings.processID==None:
            print('      cycle: {0}/{1}    Run:  {2}/{3}'.format(cycle,self.settings.numCyclesPerRun-1,self.runNum +1 ,self.numRuns))


        # write statistics to output file
        self._collectLatencyStats().items()
        self._fileWriteStats(
            dict(
                {
                    'runNum':              self.runNum,
                    'cycle':               cycle,
                }.items() +
                self._collectSumMoteStats().items()  +
                self._collectScheduleStats().items()
            )
        )

        # schedule next statistics collection
        self.engine.scheduleAtAsn(
            asn         = self.engine.getAsn()+self.settings.slotframeLength,
            cb          = self._actionEndCycle,
            uniqueTag   = (None,'_actionEndCycle'),
            priority    = 10,
        )

    def _actionEnd(self):
        '''Called once at end of the simulation.'''
        self._fileWriteTopology()

    #=== collecting statistics

    def _collectSumMoteStats(self):
        returnVal = {}

        for mote in self.engine.motes:
            moteStats        = mote.getMoteStats()
            if not returnVal:
                returnVal    = moteStats
            else:
                for k in returnVal.keys():
                   returnVal[k] += moteStats[k]

        return returnVal

    def _collectLatencyStats(self):

        motes = [m for m in self.engine.motes if m.getAveLatency() != 0 and m.dagRank >= 0]
        #print motes
        for mote in motes :
            if mote.dagRank not in self.latencyPerRank :
                self.latencyPerRank[mote.dagRank] = [mote.getAveLatency()]
            self.latencyPerRank[mote.dagRank] += [mote.getAveLatency()]
            
        #print self.latencyPerRank
        return self.latencyPerRank
            
    def _collectScheduleStats(self):

        # compute the number of schedule collisions

        # Note that this cannot count past schedule collisions which have been relocated by 6top
        # as this is called at the end of cycle
        scheduleCollisions = 0
        txCells = []
        for mote in self.engine.motes:
            for (ts,cell) in mote.schedule.items():
                (ts,ch) = (ts,cell['ch'])
                if cell['dir']==mote.DIR_TX:
                    if (ts,ch) in txCells:
                        scheduleCollisions += 1
                    else:
                        txCells += [(ts,ch)]

        # collect collided links
        txLinks = {}
        openLinks = []
        answers = []
        requests = []
        for mote in self.engine.motes:
            for (ts,cell) in mote.schedule.items():
                if cell['dir']==mote.DIR_TX:
                    (ts,ch) = (ts,cell['ch'])
                    (tx,rx) = (mote,cell['neighbor'])
                    if (ts,ch) in txLinks:
                        txLinks[(ts,ch)] += [(tx,rx)]
                    else:
                        txLinks[(ts,ch)]  = [(tx,rx)]
                else :
                    if mote.pktToSend and cell['dir'] == mote.DIR_SHARED:
                        if mote.pktToSend['type'] == 'CONTROL' and mote.pktToSend['dmac'].pktToSend and mote.pktToSend['dmac'].pktToSend['type'] == 'CONTROL':
                            openLinks += [(mote,mote.pktToSend['dmac'])]
                            if mote.pktToSend['data'][4] == "answer" or mote.pktToSend['dmac'].pktToSend == "answer" :
                                answers += [(mote,mote.pktToSend['dmac'])]
                            if mote.pktToSend['data'][4] == "req" or mote.pktToSend['dmac'].pktToSend == "req" :
                                requests += [(mote,mote.pktToSend['dmac'])]
                            
        collidedLinks = [txLinks[(ts,ch)] for (ts,ch) in txLinks if len(txLinks[(ts,ch)])>=2]
        # compute the number of Tx in schedule collision cells
        collidedTxs = 0
        collidedControls = 0
        
        for links in collidedLinks:
            collidedTxs += len(links)

        openLinks = list(set(openLinks))
        answers = list(set(answers))
        requests = list(set(requests))
                        
        collidedControls = len(openLinks)
        collidedAnswers = len(answers)
        collidedRequests = len(requests)
        
        # compute the number of effective collided Tx
        effectiveCollidedTxs = 0
        insufficientLength   = 0
        for links in collidedLinks:
            for (tx1,rx1) in links:
                for (tx2,rx2) in links:
                    if tx1!=tx2 and rx1!=rx2:
                        # check whether interference from tx1 to rx2 is effective
                        if tx1.getRSSI(rx2) > rx2.minRssi:
                            effectiveCollidedTxs += 0

        effectiveCollidedControls = 0
        insufficientLength   = 0
        for (tx1,rx1) in openLinks:
            for (tx2,rx2) in openLinks:
                if tx1!=tx2 and rx1!=rx2:
                    # check whether interference from tx1 to rx2 is effective
                    if tx1.getRSSI(rx2) > rx2.minRssi:
                        effectiveCollidedControls += 1

        return {'scheduleCollisions':scheduleCollisions, 'collidedTxs': collidedTxs, 'effectiveCollidedTxs': effectiveCollidedTxs, 'collidedControls' : collidedControls, 'effectiveCollidedControls' : effectiveCollidedControls, 'collidedAnswers' : collidedAnswers, 'collidedRequests' : collidedRequests}

    #=== writing to file

    def _fileWriteHeader(self):
        output          = []
        output         += ['## {0} = {1}'.format(k,v) for (k,v) in self.settings.__dict__.items() if not k.startswith('_')]
        output         += ['\n']
        output          = '\n'.join(output)

        with open(self.settings.getOutputFile(),'w') as f:
            f.write(output)

    def _fileWriteStats(self,stats):
        output          = []

        # columnNames
        if not self.columnNames:
            self.columnNames = sorted(stats.keys())
            output     += ['\n# '+' '.join(self.columnNames)]

        # dataline
        formatString    = ' '.join(['{{{0}:>{1}}}'.format(i,len(k)) for (i,k) in enumerate(self.columnNames)])
        formatString   += '\n'

        vals = []
        for k in self.columnNames:
            if type(stats[k])==float:
                vals += ['{0:.3f}'.format(stats[k])]
            else:
                vals += [stats[k]]

        output += ['  '+formatString.format(*tuple(vals))]

        # write to file
        with open(self.settings.getOutputFile(),'a') as f:
            f.write('\n'.join(output))

    def _fileWriteTopology(self):
        output  = []
        output += [
            '#pos runNum={0} {1}'.format(
                self.runNum,
                ' '.join(['{0}@({1:.5f},{2:.5f})@{3}'.format(mote.id,mote.x,mote.y,mote.rank) for mote in self.engine.motes])
            )
        ]
        links = {}
        for m in self.engine.motes:
            for n in self.engine.motes:
                if m==n:
                    continue
                if (n,m) in links:
                    continue
                try:
                    links[(m,n)] = (m.getRSSI(n),m.getPDR(n))
                except KeyError:
                    pass
        output += [
            '#links runNum={0} {1}'.format(
                self.runNum,
                ' '.join(['{0}-{1}@{2:.0f}dBm@{3:.3f}'.format(moteA.id,moteB.id,rssi,pdr) for ((moteA,moteB),(rssi,pdr)) in links.items()])
            )
        ]
        output += [
            '#aveChargePerCycle runNum={0} {1}'.format(
                self.runNum,
                ' '.join(['{0}@{1:.2f}'.format(mote.id,mote.getMoteStats()['chargeConsumed']/self.settings.numCyclesPerRun) for mote in self.engine.motes])
            )
        ]
        output  = '\n'.join(output)

        with open(self.settings.getOutputFile(),'a') as f:
            f.write(output)
