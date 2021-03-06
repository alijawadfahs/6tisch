
#!/usr/bin/python
'''
\brief Model of a 6TiSCH mote.

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
log = logging.getLogger('Mote')
log.setLevel(logging.DEBUG)
log.addHandler(NullHandler())

#============================ imports =========================================

import copy
import random
import threading
import math
import sys, traceback

import SimEngine
import SimSettings
import Propagation
import Topology

#============================ defines =========================================

#============================ body ============================================
class pendingTransaction:
    def __init__(self, type, neighbor, cells, sequenceNum):
        self.type = type
        self.neighbor = neighbor
        self.cells = cells
        self.sequenceNum = sequenceNum
    
class Mote(object):

    # sufficient num. of tx to estimate pdr by ACK
    NUM_SUFFICIENT_TX                  = 10
    # maximum number of tx for history
    NUM_MAX_HISTORY                    = 32
    
    DIR_TX                             = 'TX'
    DIR_RX                             = 'RX'
    DIR_SHARED                         = 'SHARED'

    DEBUG                              = 'DEBUG'
    INFO                               = 'INFO'
    WARNING                            = 'WARNING'
    ERROR                              = 'ERROR'

    #=== app
    APP_TYPE_DATA                      = 'DATA'
    APP_TYPE_CONTROL                   = 'CONTROL'
    #=== rpl
    RPL_PARENT_SWITCH_THRESHOLD        = 768 # corresponds to 1.5 hops. 6tisch minimal draft use 384 for 2*ETX.
    RPL_MIN_HOP_RANK_INCREASE          = 256
    RPL_MAX_ETX                        = 4
    RPL_MAX_RANK_INCREASE              = RPL_MAX_ETX*RPL_MIN_HOP_RANK_INCREASE*2 # 4 transmissions allowed for rank increase for parents
    RPL_MAX_TOTAL_RANK                 = 256*RPL_MIN_HOP_RANK_INCREASE*2 # 256 transmissions allowed for total path cost for parents
    RPL_PARENT_SET_SIZE                = 3
    DEFAULT_DIO_INTERVAL_MIN           = 3 # log2(DIO_INTERVAL_MIN), with DIO_INTERVAL_MIN expressed in ms
    DEFAULT_DIO_INTERVAL_DOUBLINGS     = 20 # maximum number of doublings of DIO_INTERVAL_MIN (DIO_INTERVAL_MAX = 2^(DEFAULT_DIO_INTERVAL_MIN+DEFAULT_DIO_INTERVAL_DOUBLINGS) ms)
    DEFAULT_DIO_REDUNDANCY_CONSTANT    = 10 # number of hearings to suppress next transmission in the current interval

    #=== otf
    OTF_TRAFFIC_SMOOTHING              = 0.5
    #=== 6top
    TOP_CQUEUE_SIZE                    = 50
    TOP_CQUEUEH_SIZE                   = 50
    TOP_CQUEUEN_SIZE                   = 50
    #=== tsch
    TSCH_QUEUE_SIZE                    = 50
    TSCH_MAXTXRETRIES                  = 5
    #=== radio
    RADIO_MAXDRIFT                     = 30 # in ppm
    #=== battery
    # see A Realistic Energy Consumption Model for TSCH Networks.
    # Xavier Vilajosana, Qin Wang, Fabien Chraim, Thomas Watteyne, Tengfei
    # Chang, Kris Pister. IEEE Sensors, Vol. 14, No. 2, February 2014.
    CHARGE_Idle_uC                     = 24.60
    CHARGE_TxDataRxAck_uC              = 64.82
    CHARGE_TxData_uC                   = 49.37
    CHARGE_RxDataTxAck_uC              = 76.90
    CHARGE_RxData_uC                   = 64.65

    def __init__(self,id):

        # store params
        self.id                        = id
        # local variables
        self.dataLock                  = threading.RLock()

        self.engine                    = SimEngine.SimEngine()
        self.settings                  = SimSettings.SimSettings()
        self.propagation               = Propagation.Propagation()

        # app
        self.pkPeriod                  = self.settings.pkPeriod
        # role
        self.dagRoot                   = False
        # rpl
        self.rank                      = None
        self.dagRank                   = None
        self.parentSet                 = []
        self.preferredParent           = None
        self.rplRxDIO                  = {}                    # indexed by neighbor, contains int
        self.neighborRank              = {}                    # indexed by neighbor
        self.neighborDagRank           = {}                    # indexed by neighbor
        self.trafficPortionPerParent   = {}                    # indexed by parent, portion of outgoing traffic
        # otf
        self.otfSF                     = 0
        self.otfStatus                 = {}
        self.asnOTFevent               = None
        self.otfHousekeepingPeriod     = self.settings.otfHousekeepingPeriod
        self.timeBetweenOTFevents      = []
        self.inTraffic                 = {}                    # indexed by neighbor
        self.inTrafficMovingAve        = {}                    # indexed by neighbor
        # 6top
        self.sequenceNumberWithNeighbor  = {}
        self.sequenceNumberFromNeighbor = {}
        self.ignorePacket              = []
        self.droppedControl            = []
        self.transactionTimeout        = 20
        self.transactionRetries        = 0
        self.pendingTransaction        = None
        self.numCellsToNeighbors       = {}                    # indexed by neighbor, contains int
        self.numCellsFromNeighbors     = {}                    # indexed by neighbor, contains int
        # changing this threshold the detection of a bad cell can be
        # tuned, if as higher the slower to detect a wrong cell but the more prone
        # to avoid churn as lower the faster but with some chances to introduces
        # churn due to unstable medium
        self.topPdrThreshold           = self.settings.topPdrThreshold
        self.topHousekeepingPeriod     = self.settings.topHousekeepingPeriod
        # tsch
        self.macMinBE = 1
        self.macMaxBE = 7
        self.macBackoffNB = 0
        self.macMaxCSMABackoffs = 4
        self.backoffExponent       = self.macMinBE
        self.sendcontrolDelay       = 0
        self.sendcontrolFailed     = False
        self.requestTriggered      = {}
        self.txQueue               = []
        self.controlQueue          = []
        # normal priority queue
        self.controlQueueNP        = []
        # high priority queue
        self.controlQueueHP        = []
        self.cellsAllocToNeighbor  = {}
        self.pktToSend                 = None
        self.pktToSendAlloc        = None
        self.schedule                  = {}                    # indexed by ts, contains cell
        self.reserve                   = [[False]*self.settings.numChans for _ in range(self.settings.slotframeLength)]
        if self.settings.queuing != 0 :
            self.waitingFor                = self.DIR_SHARED
        else :
            self.waitingFor            = None
        self.hasSendControl	       = False
        self.sharedSlots               = []
        self.timeCorrectedSlot         = None
        # radio
        self.txPower                   = 0                     # dBm
        self.antennaGain               = 0                     # dBi
        self.minRssi                   = self.settings.minRssi # dBm
        self.noisepower                = -105                  # dBm
        self.drift                     = random.uniform(-self.RADIO_MAXDRIFT, self.RADIO_MAXDRIFT)
        # wireless
        self.RSSI                      = {}                    # indexed by neighbor
        self.PDR                       = {}                    # indexed by neighbor
        # location
        # battery
        self.chargeConsumed            = 0

        # stats
        self._resetMoteStats()
        self._resetQueueStats()
        self._resetLatencyStats()
        self._resetHopsStats()
        self._resetRadioStats()

    #======================== stack ===========================================

    #===== role

    def role_setDagRoot(self):
        self.dagRoot              = True
        self.rank                 = 0
        self.dagRank              = 0
        self.packetLatencies      = [] # in slots
        self.packetHops           = []

    #===== application
    def _app_schedule_sendControl(self,init=False,cells=None, numCells=None, type=None, neighb=None, dir=None, usedSlots = None, value = None):
        ''' create an event that is inserted into the simulator engine to send the control according to the traffic'''

	asn    = self.engine.getAsn()
        ts     = asn%self.settings.slotframeLength
        
        if not init:
            cycle = int(math.ceil(self.settings.dioPeriod/(self.settings.slotframeLength*self.settings.slotDuration)))
        else:
            cycle = 1


        if type == "answer" :
            priority = 11
        else :
            priority = 10
            
        # if no tx cells for the neighbor : send through slot 0
        delay = self.settings.slotDuration + random.randint(0, pow(2, self.backoffExponent) -1)
        
        #if not self.getTxCells() or not self.settings.opportunist : # and not (ts in (t for t in self.schedule))  :
        if neighb not in self.sequenceNumberWithNeighbor :
            self.sequenceNumberWithNeighbor[neighb] = 0
        self.sequenceNumberWithNeighbor[neighb] += 1
        self.engine.scheduleIn(
                delay       = delay,
                cb          = self._app_action_sendControl,
                args         = [cells,numCells, neighb, dir,type, self, usedSlots, value, self.sequenceNumberWithNeighbor[neighb]],
                uniqueTag   = (self.id, 'sendControl'),
                priority    = priority,
            )        
            

    def _app_schedule_sendData(self,init=False):
        ''' create an event that is inserted into the simulator engine to send the data according to the traffic'''

        if not init:
            # compute random delay
            delay       = self.pkPeriod*(1+random.uniform(-self.settings.pkPeriodVar,self.settings.pkPeriodVar))
        else:
            # compute initial time within the range of [next asn, next asn+pkPeriod]
            delay       = self.settings.slotDuration + self.pkPeriod*random.random()

        assert delay>0
        # schedule
        self.engine.scheduleIn(
            delay       = delay,
            cb          = self._app_action_sendData,
            args        = None,
            uniqueTag   = (self.id, 'sendData'),
            priority    = 2,
        )

    def _app_schedule_enqueueData(self):
        ''' create an event that is inserted into the simulator engine to send a data burst'''

        # schedule numPacketsBurst packets at burstTime
        for i in xrange(self.settings.numPacketsBurst):
            self.engine.scheduleIn(
                delay       = self.settings.burstTime,
                cb          = self._app_action_enqueueData,
                args        = None,
                uniqueTag   = (self.id, 'enqueueData'),
                priority    = 2,
            )

    def _app_action_sendControl(self, args=None):
        # enqueue control
        assert args != None
        self._app_action_enqueueControl(args)


    def _app_action_sendData(self, args=None):
        ''' actual send data function. Evaluates queue length too '''

        # enqueue data
        self._app_action_enqueueData()

        # schedule next _app_action_sendData
        self._app_schedule_sendData()

    def _app_action_enqueueControl(self, args=None):
        ''' actual enqueue control function '''

        #self._log(self.DEBUG,"[app] _app_action_sendData")

        # only start sending the control if I have some TX cells
    
        request = "top"
        newPacket = {
            'asn':          self.engine.getAsn(),
            'type':         self.APP_TYPE_CONTROL,
            'data':         args,
            'dmac':         args[2],
            'smac':         args[5],
            'payload':      [self.id,self.engine.getAsn(),1], # the payload is used for latency and number of hops calculation
            'retriesLeft':  self.TSCH_MAXTXRETRIES
        }

        # enqueue packet in TSCH queue

        isEnqueued = self._tsch_enqueueSlotZero(newPacket)
        if not isEnqueued:
            # update mote stats
            self._incrementMoteStats('droppedAppFailedEnqueueControl')
           

    def _app_action_enqueueData(self):
        ''' actual enqueue data function '''

        #self._log(self.DEBUG,"[app] _app_action_sendData")

        # only start sending data if I have some TX cells
        if self.getTxCells():

            # create new packet
            newPacket = {
                'asn':            self.engine.getAsn(),
                'type':           self.APP_TYPE_DATA,
                'data':           None,
                'payload':        [self.id,self.engine.getAsn(),1], # the payload is used for latency and number of hops calculation
                'retriesLeft':    self.TSCH_MAXTXRETRIES
            }

            # update mote stats
            self._incrementMoteStats('appGenerated')

            # enqueue packet in TSCH queue
            isEnqueued = self._tsch_enqueue(newPacket)

            if not isEnqueued:
                # update mote stats
                self._incrementMoteStats('droppedAppFailedEnqueueData')


    #===== rpl

    def _rpl_schedule_sendDIO(self,init=False):

        with self.dataLock:

            asn    = self.engine.getAsn()
            ts     = asn%self.settings.slotframeLength

            if not init:
                cycle = int(math.ceil(self.settings.dioPeriod/(self.settings.slotframeLength*self.settings.slotDuration)))
            else:
                cycle = 1

            # schedule at start of next cycle
            self.engine.scheduleAtAsn(
                asn         = asn-ts+cycle*self.settings.slotframeLength,
                cb          = self._rpl_action_sendDIO,
                uniqueTag   = (self.id,'DIO'),
                priority    = 3,
            )


    def _rpl_action_checkRPL(self):
        parentSet=[(parent.id, parent.rank) for parent in self.parentSet]
        if parentSet:
            max_parent_id, max_parent_rank = max(parentSet,key=lambda x:x[1])
            if self.rank<=max_parent_rank:
                print self.id, self.rank
                print parentSet
            assert self.rank>max_parent_rank

    def _rpl_action_sendDIO(self, args=None):

        #self._log(self.DEBUG,"[rpl] _rpl_action_sendDIO")

        with self.dataLock:

            if self.rank!=None and self.dagRank!=None:
                #print "Send DIO"
                # update mote stats
                self._incrementMoteStats('rplTxDIO')

                # log charge usage for sending DIO is currently neglected
                # self._logChargeConsumed(self.CHARGE_TxData_uC)

                # "send" DIO to all neighbors
                for neighbor in self._myNeigbors():

                    # don't update DAGroot
                    if neighbor.dagRoot:
                        continue

                    # don't update poor link
                    if neighbor._rpl_calcRankIncrease(self)>self.RPL_MAX_RANK_INCREASE:
                        continue

                    # log charge usage (for neighbor) for receiving DIO is currently neglected
                    # neighbor._logChargeConsumed(self.CHARGE_RxData_uC)

                    # in neighbor, update my rank/DAGrank
                    neighbor.neighborDagRank[self]    = self.dagRank
                    neighbor.neighborRank[self]       = self.rank

                    # in neighbor, update number of DIOs received
                    if self not in neighbor.rplRxDIO:
                        neighbor.rplRxDIO[self]  = 0
                    neighbor.rplRxDIO[self]     += 1

                    # update mote stats
                    self._incrementMoteStats('rplRxDIO')

                    # skip useless housekeeping
                    if not neighbor.rank or self.rank<neighbor.rank:
                        # in neighbor, do RPL housekeeping
                        neighbor._rpl_housekeeping()

                    # update time correction
                    if neighbor.preferredParent == self:
                        asn                        = self.engine.getAsn()
                        neighbor.timeCorrectedSlot = asn
                        neighbor._otf_housekeeping()
            # schedule to send the next DIO
            
            self._rpl_schedule_sendDIO()

    def _rpl_housekeeping(self):
        with self.dataLock:

            #===
            # refresh the following parameters:
            # - self.preferredParent
            # - self.rank
            # - self.dagRank
            # - self.parentSet

            # calculate my potential rank with each of the motes I have heard a DIO from
            potentialRanks = {}
            for (neighbor,neighborRank) in self.neighborRank.items():
                if neighbor not in self.sequenceNumberWithNeighbor :
                    self.sequenceNumberWithNeighbor[neighbor] = 0
                 #if neighbor not in self.sequenceNumberToNeighbor :
                 #   self.sequenceNumberFromNeighbor[neighbor] = 0
                # calculate the rank increase to that neighbor
                rankIncrease = self._rpl_calcRankIncrease(neighbor)
                if rankIncrease!=None and rankIncrease<=min([self.RPL_MAX_RANK_INCREASE, self.RPL_MAX_TOTAL_RANK-neighborRank]):
                    # record this potential rank
                    potentialRanks[neighbor] = neighborRank+rankIncrease

            # sort potential ranks
            sorted_potentialRanks = sorted(potentialRanks.iteritems(), key=lambda x:x[1])

            # switch parents only when rank difference is large enough
            for i in range(1,len(sorted_potentialRanks)):
                if sorted_potentialRanks[i][0] in self.parentSet:
                    # compare the selected current parent with motes who have lower potential ranks
                    # and who are not in the current parent set
                    for j in range(i):
                        if sorted_potentialRanks[j][0] not in self.parentSet:
                            if sorted_potentialRanks[i][1]-sorted_potentialRanks[j][1]<self.RPL_PARENT_SWITCH_THRESHOLD:
                                mote_rank = sorted_potentialRanks.pop(i)
                                sorted_potentialRanks.insert(j,mote_rank)
                                break

            # pick my preferred parent and resulting rank
            if sorted_potentialRanks:
                oldParentSet = set([parent.id for parent in self.parentSet])

                (newPreferredParent,newrank) = sorted_potentialRanks[0]

                # compare a current preferred parent with new one
                if self.preferredParent and newPreferredParent!=self.preferredParent:
                    for (mote,rank) in sorted_potentialRanks[:self.RPL_PARENT_SET_SIZE]:

                        if mote == self.preferredParent:
                            # switch preferred parent only when rank difference is large enough
                            if rank-newrank<self.RPL_PARENT_SWITCH_THRESHOLD:
                                (newPreferredParent,newrank) = (mote,rank)

                    # update mote stats
                    self._incrementMoteStats('rplChurnPrefParent')
                    # log
                    self._log(
                        self.INFO,
                        "[rpl] churn: preferredParent {0}->{1}",
                        (self.preferredParent.id,newPreferredParent.id),
                    )

                # update mote stats
                if self.rank and newrank!=self.rank:
                    self._incrementMoteStats('rplChurnRank')
                    # log
                    self._log(
                        self.INFO,
                        "[rpl] churn: rank {0}->{1}",
                        (self.rank,newrank),
                    )

                # store new preferred parent and rank
                (self.preferredParent,self.rank) = (newPreferredParent,newrank)

                # calculate DAGrank
                self.dagRank = int(self.rank/self.RPL_MIN_HOP_RANK_INCREASE)

                # pick my parent set
                self.parentSet = [n for (n,_) in sorted_potentialRanks if self.neighborRank[n]<self.rank][:self.RPL_PARENT_SET_SIZE]
                assert self.preferredParent in self.parentSet

                if oldParentSet!=set([parent.id for parent in self.parentSet]):
                    self._incrementMoteStats('rplChurnParentSet')

            #===
            # refresh the following parameters:
            # - self.trafficPortionPerParent

            etxs        = dict([(p, 1.0/(self.neighborRank[p]+self._rpl_calcRankIncrease(p))) for p in self.parentSet])
            sumEtxs     = float(sum(etxs.values()))
            self.trafficPortionPerParent = dict([(p, etxs[p]/sumEtxs) for p in self.parentSet])

            transaction = False
            for neighbor in self.numCellsToNeighbors.keys() :
                if neighbor.pendingTransaction != None and self == neighbor.pendingTransaction.neighbor :
                    transaction = True
            # remove TX cells to neighbor who are not in parent set
            for neighbor in self.numCellsToNeighbors.keys() : #[neighbor for neighbor in self.numCellsToNeighbors.keys() if ]:
                if self.pendingTransaction != None and neighbor == self.pendingTransaction.neighbor or transaction :
                    return
                if neighbor not in self.parentSet:
                    # log
                    self._log(
                        self.INFO,
                        "[otf] removing cell to {0}, since not in parentSet {1}",
                        (neighbor.id,[p.id for p in self.parentSet]),
                    )

                    tsList=[ts for ts, cell in self.schedule.iteritems() if cell['neighbor']==neighbor and cell['dir']==self.DIR_TX]
                    #print "remove from rpl " +str(self)
                    if tsList: 
                        self.top_cell_deletion_sender(neighbor,tsList)

    def _rpl_calcRankIncrease(self, neighbor):

        with self.dataLock:

            # estimate the ETX to that neighbor
            etx = self._estimateETX(neighbor)

            # return if that failed
            if not etx:
                return

            # per draft-ietf-6tisch-minimal, rank increase is 2*ETX*RPL_MIN_HOP_RANK_INCREASE
            return int(2*self.RPL_MIN_HOP_RANK_INCREASE*etx)

    #===== otf

    def _otf_schedule_housekeeping(self):

        self.engine.scheduleIn(
            delay       = self.otfHousekeepingPeriod*(0.9+0.2*random.random()),
            cb          = self._otf_housekeeping,
            args        = None,
            uniqueTag   = (self.id,'otfHousekeeping'),
            priority    = 4,
        )

    def _otf_housekeeping(self, args=None):
        '''
        OTF algorithm: decides when to add/delete cells.
        '''

        #self._log(self.DEBUG,"[otf] _otf_housekeeping")

        with self.dataLock:

            # calculate the "moving average" incoming traffic, in pkts since last cycle, per neighbor
            with self.dataLock:

                # collect all neighbors I have RX cells to
                rxNeighbors = [cell['neighbor'] for (ts,cell) in self.schedule.items() if cell['dir']==self.DIR_RX]

                # remove duplicates
                rxNeighbors = list(set(rxNeighbors))

                # reset inTrafficMovingAve
                neighbors = self.inTrafficMovingAve.keys()
                for neighbor in neighbors:
                    if neighbor not in rxNeighbors:
                        del self.inTrafficMovingAve[neighbor]

                # set inTrafficMovingAve
                for neighbor in rxNeighbors:
                    if neighbor in self.inTrafficMovingAve:
                        newTraffic  = 0
                        newTraffic += self.inTraffic[neighbor]*self.OTF_TRAFFIC_SMOOTHING               # new
                        newTraffic += self.inTrafficMovingAve[neighbor]*(1-self.OTF_TRAFFIC_SMOOTHING)  # old
                        self.inTrafficMovingAve[neighbor] = newTraffic
                    elif self.inTraffic[neighbor] != 0:
                        self.inTrafficMovingAve[neighbor] = self.inTraffic[neighbor]

            # reset the incoming traffic statistics, so they can build up until next housekeeping
            self._otf_resetInTraffic()

            # calculate my total generated traffic, in pkt/s
            genTraffic       = 0
            genTraffic      += 1.0/self.pkPeriod # generated by me
            for neighbor in self.inTrafficMovingAve:
                genTraffic  += self.inTrafficMovingAve[neighbor]/self.otfHousekeepingPeriod   # relayed
            # convert to pkts/cycle
            genTraffic      *= self.settings.slotframeLength*self.settings.slotDuration
            remainingPortion = 0.0
            parent_portion = self.trafficPortionPerParent.items()
            # sort list so that the parent assigned larger traffic can be checked first
            sorted_parent_portion = sorted(parent_portion, key = lambda x: x[1], reverse=True)
            
            # split genTraffic across parents, trigger 6top to add/delete cells accordingly
            for (parent,portion) in sorted_parent_portion:

                # if some portion is remaining, this is added to this parent
                if remainingPortion != 0.0:
                    portion                             += remainingPortion
                    remainingPortion                     = 0.0
                    self.trafficPortionPerParent[parent] = portion

                # calculate required number of cells to that parent
                etx = self._estimateETX(parent)
                if etx>self.RPL_MAX_ETX: # cap ETX
                    etx  = self.RPL_MAX_ETX
                reqCells      = int(math.ceil(portion*genTraffic*etx))
                # calculate the OTF threshold
                threshold     = int(math.ceil(portion*self.settings.otfThreshold))

                # measure how many cells I have now to that parent
                nowCells      = self.numCellsToNeighbors.get(parent,0)
                if (nowCells - reqCells < 0) :
                    # notice children
                    rxNeighbors = [cell['neighbor'] for (ts,cell) in self.schedule.items() if cell['dir']==self.DIR_RX]
                    rxNeighbors = list(set(rxNeighbors))
                    for neighbor in rxNeighbors :
                        #self._app_schedule_sendControl(neighb = neighbor, type = "OTF", value = "STOP")
                        txNeighborsOfNeighbors = [cell['neighbor'] for (ts,cell) in neighbor.schedule.items() if cell['dir']==self.DIR_TX]
                        if int(math.ceil(self.engine.getAsn()/(self.settings.slotframeLength))) != self.otfSF and self in txNeighborsOfNeighbors:
                            self.otfSF = int(math.ceil(self.engine.getAsn()/(self.settings.slotframeLength)))
                            neighbor.otfStatus[self] = "STOP"
                            #print "signals STOP"
                else :
                    # notice children
                    rxNeighbors = [cell['neighbor'] for (ts,cell) in self.schedule.items() if cell['dir']==self.DIR_RX]
                    rxNeighbors = list(set(rxNeighbors))
                    for neighbor in rxNeighbors :
                        #self._app_schedule_sendControl(neighb = neighbor, type = "OTF", value = "STOP")
                        if int(math.ceil(self.engine.getAsn()/(self.settings.slotframeLength))) != self.otfSF :
                            self.otfSF = int(math.ceil(self.engine.getAsn()/(self.settings.slotframeLength)))
                            neighbor.otfStatus[self] = "START"
                            #print "signals START"

                            
                if nowCells<reqCells:
                    # I don't have enough cells
                    self.engine.cellNeeded += (reqCells - nowCells)
                    # calculate how many to add
                    numCellsToAdd = reqCells-nowCells+(threshold+1)/2

                    # log
                    self._log(
                        self.INFO,
                        "[otf] not enough cells to {0}: have {1}, need {2}, add {3}",
                        (parent.id,nowCells,reqCells,numCellsToAdd),
                    )

                    # update mote stats
                    self._incrementMoteStats('otfAdd')

                    # have 6top add cells
                    #if not self.dagRank == 0 :
                    #self.requestTrigerred = True
                    self._top_cell_reservation_request(args = None, neighbor = parent,numCells = numCellsToAdd)
                    #print "request triggered"
                    # measure how many cells I have now to that parent
                    nowCells     = self.numCellsToNeighbors.get(parent,0)

                    # store handled portion and remaining portion
                    if nowCells<reqCells:
                        handledPortion   = (float(nowCells)/etx)/genTraffic
                        remainingPortion = portion - handledPortion
                        self.trafficPortionPerParent[parent] = handledPortion

                    # remember OTF triggered
                    otfTriggered = True

                elif reqCells<nowCells-threshold:
                    # I have too many cells

                    # calculate how many to remove
                    numCellsToRemove = nowCells-reqCells-(threshold+1)/2

                    # log
                    self._log(
                        self.INFO,
                        "[otf] too many cells to {0}:  have {1}, need {2}, remove {3}",
                        (parent.id,nowCells,reqCells,numCellsToRemove),
                    )

                    # update mote stats
                    self._incrementMoteStats('otfRemove')

                    # have 6top remove cells
                    self._top_removeCells(parent,numCellsToRemove)

                    # remember OTF triggered
                    otfTriggered = True
                    
                else:
                    # nothing to do

                    # remember OTF did NOT trigger
                    otfTriggered = False

                # maintain stats
                if otfTriggered:
                    now = self.engine.getAsn()
                    if not self.asnOTFevent:
                        assert not self.timeBetweenOTFevents
                    else:
                        self.timeBetweenOTFevents += [now-self.asnOTFevent]
                    self.asnOTFevent = now

            # schedule next housekeeping
            self._otf_schedule_housekeeping()


            
    def _otf_resetInTraffic(self):
        with self.dataLock:
            for neighbor in self._myNeigbors():
                self.inTraffic[neighbor] = 0

    def _otf_incrementIncomingTraffic(self,neighbor):
        with self.dataLock:
            self.inTraffic[neighbor] += 1

    def _otf_decrementIncomingTraffic(self, neighbor):
        with self.dataLock:
            self.inTraffic[neighbor] -= 1


    #===== 6top

    def _top_schedule_housekeeping(self):

        self.engine.scheduleIn(
            delay       = self.topHousekeepingPeriod*(0.9+0.2*random.random()),
            cb          = self._top_housekeeping,
            args        = None,
            uniqueTag   = (self.id,'topHousekeeping'),
            priority    = 5,
        )

    def _top_housekeeping(self, args=None):
        '''
        For each neighbor I have TX cells to, relocate cells if needed.
        '''


        # tx-triggered housekeeping
        #'''
        # collect all neighbors I have TX cells to
        txNeighbors = [cell['neighbor'] for (ts,cell) in self.schedule.items() if cell['dir']==self.DIR_TX]

        # remove duplicates
        txNeighbors = list(set(txNeighbors))

        for neighbor in txNeighbors:
            nowCells = self.numCellsToNeighbors.get(neighbor,0)
            #if self.settings.queuing != 0 and nowCells != len([t for (t,c) in self.schedule.items() if c['dir']==self.DIR_TX and c['neighbor']==neighbor]) :
            #    self.numCellsToNeighbors[neighbor] = len([t for (t,c) in self.schedule.items() if c['dir']==self.DIR_TX and c['neighbor']==neighbor])
            #elif self.settings.queuing == 0 :
            assert nowCells == len([t for (t,c) in self.schedule.items() if c['dir']==self.DIR_TX and c['neighbor']==neighbor])

        # do some housekeeping for each neighbor
        for neighbor in txNeighbors:
            self._top_txhousekeeping_per_neighbor(neighbor)

        #'''

        # rx-triggered housekeeping
        #'''
        # collect neighbors from which I have RX cells that is detected as collision cell
        rxNeighbors = [cell['neighbor'] for (ts,cell) in self.schedule.items() if cell['dir']==self.DIR_RX and cell['rxDetectedCollision']]

        # remove duplicates
        rxNeighbors = list(set(rxNeighbors))

        for neighbor in rxNeighbors:
            nowCells = self.numCellsFromNeighbors.get(neighbor,0)
            assert nowCells == len([t for (t,c) in self.schedule.items() if c['dir']==self.DIR_RX and c['neighbor']==neighbor])

        # do some housekeeping for each neighbor
        for neighbor in rxNeighbors:
            self._top_rxhousekeeping_per_neighbor(neighbor)
        #'''

        self._top_schedule_housekeeping()



    def _top_rxhousekeeping_per_neighbor(self,neighbor):

        rxCells = [(ts,cell) for (ts,cell) in self.schedule.items() if cell['dir']==self.DIR_RX and cell['rxDetectedCollision'] and cell['neighbor']==neighbor]

        relocation = False
        for ts,cell in rxCells:

            # measure how many cells I have now from that child
            nowCells = self.numCellsFromNeighbors.get(neighbor,0)
            
            # relocate: add new first
            
            self._top_cell_reservation_request(args = None, neighbor = neighbor,numCells = 1,dir=self.DIR_RX)

            # relocate: remove old only when successfully added
            if nowCells < self.numCellsFromNeighbors.get(neighbor,0):
                neighbor.top_cell_deletion_sender(self,[ts])

                # remember I relocated a cell
                relocation = True

        if relocation:
            # update stats
            self._incrementMoteStats('topRxRelocatedCells')



    def _top_txhousekeeping_per_neighbor(self,neighbor):
        '''
        For a particular neighbor, decide to relocate cells if needed.
        '''

        #===== step 1. collect statistics:

        # pdr for each cell
        cell_pdr = []
        for (ts,cell) in self.schedule.items():
            if cell['neighbor']==neighbor and cell['dir']==self.DIR_TX:
                # this is a TX cell to that neighbor

                # abort if not enough TX to calculate meaningful PDR
                if cell['numTx']<self.NUM_SUFFICIENT_TX:
                    continue

                # calculate pdr for that cell
                recentHistory = cell['history'][-self.NUM_MAX_HISTORY:]
                pdr = float(sum(recentHistory)) / float(len(recentHistory))

                # store result
                cell_pdr += [(ts,pdr)]

        # pdr for the bundle as a whole
        bundleNumTx     = sum([len(cell['history'][-self.NUM_MAX_HISTORY:]) for cell in self.schedule.values() if cell['neighbor']==neighbor and cell['dir']==self.DIR_TX])
        bundleNumTxAck  = sum([sum(cell['history'][-self.NUM_MAX_HISTORY:]) for cell in self.schedule.values() if cell['neighbor']==neighbor and cell['dir']==self.DIR_TX])
        if bundleNumTx<self.NUM_SUFFICIENT_TX:
            bundlePdr   = None
        else:
            bundlePdr   = float(bundleNumTxAck) / float(bundleNumTx)

        #===== step 2. relocate worst cell in bundle, if any
        # this step will identify the cell with the lowest PDR in the bundle.
        # It it's PDR is self.topPdrThreshold lower than the average of the bundle
        # this step will move that cell.

        relocation = False

        if cell_pdr:

            # identify the cell with worst pdr, and calculate the average

            worst_ts   = None
            worst_pdr  = None

            for (ts,pdr) in cell_pdr:
                if worst_pdr==None or pdr<worst_pdr:
                    worst_ts  = ts
                    worst_pdr = pdr

            assert worst_ts!=None
            assert worst_pdr!=None

            # ave pdr for other cells
            othersNumTx     = sum([len(cell['history'][-self.NUM_MAX_HISTORY:]) for (ts,cell) in self.schedule.items() if cell['neighbor']==neighbor and cell['dir']==self.DIR_TX and ts != worst_ts])
            othersNumTxAck  = sum([sum(cell['history'][-self.NUM_MAX_HISTORY:]) for (ts,cell) in self.schedule.items() if cell['neighbor']==neighbor and cell['dir']==self.DIR_TX and ts != worst_ts])
            if othersNumTx<self.NUM_SUFFICIENT_TX:
                ave_pdr   = None
            else:
                ave_pdr   = float(othersNumTxAck) / float(othersNumTx)

            # relocate worst cell is "bad enough"
            if ave_pdr and worst_pdr<(ave_pdr/self.topPdrThreshold):

                # log
                self._log(
                    self.INFO,
                    "[6top] relocating cell ts {0} to {1} (pdr={2:.3f} significantly worse than others {3})",
                    (worst_ts,neighbor,worst_pdr,cell_pdr),
                )

                # measure how many cells I have now to that parent
                nowCells = self.numCellsToNeighbors.get(neighbor,0)

                # relocate: add new first
                self._top_cell_reservation_request(args = None, neighbor = neighbor, numCells = 1)

                # relocate: remove old only when successfully added
                if nowCells < self.numCellsToNeighbors.get(neighbor,0):
                    self.top_cell_deletion_sender(neighbor,[worst_ts])

                    # update stats
                    self._incrementMoteStats('topTxRelocatedCells')

                    # remember I relocated a cell for that bundle
                    relocation = True

        #===== step 3. relocate the complete bundle
        # this step only runs if the previous hasn't, and we were able to
        # calculate a bundle PDR.
        # this step verifies that the average PDR for the complete bundle is
        # expected, given the RSSI to that neighbor. If it's lower, this step
        # will move all cells in the bundle.

        bundleRelocation = False

        if (not relocation) and bundlePdr!=None:

            # calculate the theoretical PDR to that neighbor, using the measured RSSI
            rssi            = self.getRSSI(neighbor)
            theoPDR         = Topology.Topology.rssiToPdr(rssi)

            # relocate complete bundle if measured RSSI is significantly worse than theoretical
            if bundlePdr<(theoPDR/self.topPdrThreshold):
                for (ts,_) in cell_pdr:

                    # log
                    self._log(
                        self.INFO,
                        "[6top] relocating cell ts {0} to {1} (bundle pdr {2} << theoretical pdr {3})",
                        (ts,neighbor,bundlePdr,theoPDR),
                    )

                    # measure how many cells I have now to that parent
                    nowCells = self.numCellsToNeighbors.get(neighbor,0)

                    # relocate: add new first
                    self._top_cell_reservation_request(args = None, neighbor = neighbor,numCells = 1)

                    # relocate: remove old only when successfully added
                    if nowCells < self.numCellsToNeighbors.get(neighbor,0):

                        self.top_cell_deletion_sender(neighbor,[ts])

                        bundleRelocation = True

                # update stats
                if bundleRelocation:
                    self._incrementMoteStats('topTxRelocatedBundles')


    def top_add_response(self, cells, neighbor, dir) :
        with self.dataLock :
            self._app_schedule_sendControl(cells = cells, numCells = len(cells),neighb = neighbor, type = "answer", dir = dir)
            return True

    def top_add_request(self, numCellsReq, neighbor, dir, alreadyUsedSlots) :
        with self.dataLock :
            self._app_schedule_sendControl(numCells = numCellsReq, type = "req", neighb = neighbor, dir = dir, usedSlots = alreadyUsedSlots)
            return True

    def top_new_handle_request_ok(self, cells, numCells, neighbor, dir):
        with self.dataLock :
            self.requestTriggered[neighbor] = False
            cellList=[]
            for ts, ch in cells.iteritems():
                # log
                self._log(
                    self.INFO,
                    '[6top] add TX cell ts={0},ch={1} from {2} to {3}',
                    (ts,ch,self.id,neighbor.id),
                )
                cellList += [(ts,ch,dir)]
                
            alreadyHere = 0

            #check if the parent answer fits our scheduler
            for cell in cellList:
                if cell[0] in self.schedule.keys() :
                    alreadyHere += 1
                    cellList.remove(cell)

            if cellList != None :
                self._tsch_addCells(neighbor,cellList)
                # update counters
                if dir==self.DIR_TX:
                    if neighbor not in self.numCellsToNeighbors:
                        self.numCellsToNeighbors[neighbor]    = 0
                    self.numCellsToNeighbors[neighbor]  += len(cellList)
                elif dir==self.DIR_RX:
                    if neighbor not in self.numCellsFromNeighbors:
                        self.numCellsFromNeighbors[neighbor]    = 0
                    self.numCellsFromNeighbors[neighbor]  += len(cellList)

                if len(cells)!=numCells:
                    # log
                    self._log(
                        self.ERROR,
                        '[6top] scheduled {0} cells out of {1} required between motes {2} and {3}',
                        (len(cells),numCells,self.id,neighbor.id),
                    )
                    print '[6top] scheduled {0} cells out of {1} required between motes {2} and {3}'.format(len(cells),numCells,self.id,neighbor.id)
            cell = []
            for ts, ch, dir in cellList :
                cell += [ts]

            self._app_schedule_sendControl(numCells = len(cellList), cells = cell, type = "confirmation", neighb = neighbor, dir = None)
            
            
    def _top_cell_reservation_request(self,args=None, neighbor=None,numCells=None,dir=DIR_TX):
        ''' tries to reserve numCells cells to a neighbor. '''
        with self.dataLock:
            if self.pendingTransaction != None :
                #print str(self.getTxCells() == []) + " " + str(self)
                self.transactionRetries += 1
                if self.transactionRetries == self.transactionTimeout :
                    self._top_abort_transaction()
            #        print "ABORT"
                else :
                    return
            if neighbor in self.requestTriggered and self.requestTriggered[neighbor] == True or neighbor not in self.sequenceNumberWithNeighbor.keys():
                return
            self.requestTriggered[neighbor] = True
            self.pendingTransaction = pendingTransaction("moteRequest", neighbor, None,self.sequenceNumberWithNeighbor[neighbor])
            
            if (self.settings.queuing != 0 and (self.settings.bootstrap or (not self.settings.bootstrap and self.getTxCells()))):
	            self.top_add_request(numCells, neighbor,dir, self.schedule.keys())
            else :
                if self.settings.queuing == 1 :
                    #only one cell for boostrap, because not handling bootstrap using network
                    cells=neighbor.top_cell_reservation_response(self,1,dir,None, [0])
                else :
                    cells=neighbor.top_cell_reservation_response(self,numCells,dir, None, None)
                cellList=[]
                for ts, ch in cells.iteritems():
                    # log
                    self._log(
                        self.INFO,
                        '[6top] add TX cell ts={0},ch={1} from {2} to {3}',
                        (ts,ch,self.id,neighbor.id),
                        )
                    if ts not in self.schedule.keys() :
                        cellList += [(ts,ch,dir)]
                self._tsch_addCells(neighbor,cellList)
                # update counters
                if dir==self.DIR_TX:
                    if neighbor not in self.numCellsToNeighbors:
                        self.numCellsToNeighbors[neighbor]    = 0
                    self.numCellsToNeighbors[neighbor]  += len(cellList)
                else:
                    if neighbor not in self.numCellsFromNeighbors:
                        self.numCellsFromNeighbors[neighbor]    = 0
                    self.numCellsFromNeighbors[neighbor]  += len(cellList)
                    
                if len(cells)!=numCells:
                    # log
                    self._log(
                        self.ERROR,
                        '[6top] scheduled {0} cells out of {1} required between motes {2} and {3}',
                        (len(cells),numCells,self.id,neighbor.id),
                    )
                    print '[6top] scheduled {0} cells out of {1} required between motes {2} and {3}'.format(len(cells),numCells,self.id,neighbor.id)

    def top_cell_reservation_response(self,neighbor,numCells,dirNeighbor, args, slotUsedByNeighbor):
        ''' tries to reserve numCells cells to a neighbor. '''

        with self.dataLock:
            #if self not in neighbor.requestTriggered or neighbor.requestTriggered[self] == False :
            #    return
            # set direction of cells
            if dirNeighbor == self.DIR_TX:
                dir = self.DIR_RX
            else:
                dir = self.DIR_TX
                
            if self.settings.queuing != 0 :
                availableTimeslots=list(set(range(self.settings.slotframeLength))-set(slotUsedByNeighbor)-set(self.schedule.keys()))
            else :
                availableTimeslots=list(set(range(self.settings.slotframeLength))-set(neighbor.schedule.keys())-set(self.schedule.keys()))
            random.shuffle(availableTimeslots)
            cells=dict([(ts,self._choose_channel(neighbor,ts)) for ts in availableTimeslots[:numCells]])
            cellList=[]
            for ts, ch in cells.iteritems():
                # log
                self._log(
                    self.INFO,
                    '[6top] add RX cell ts={0},ch={1} from {2} to {3}',
                    (ts,ch,self.id,neighbor.id),
                )
                cellList += [(ts,ch,dir)]
            if self.settings.idealAllocation :
                cells = {}
                cellList = []
                for a in range(0, numCells) :
                    cellList += [(self.engine.getNextTS(self.sharedSlots,dir))]
                    cells[cellList[a][0]] = cellList[a][1]
            self._tsch_addCells(neighbor,cellList)
            
            if self.settings.queuing != 0 :
                if neighbor not in self.sequenceNumberWithNeighbor :
                    self.sequenceNumberWithNeighbor[neighbor] = 0
                self.pendingTransaction = pendingTransaction("parentAdds", neighbor, cellList, self.sequenceNumberWithNeighbor[neighbor])
                
            # update counters
            if dir==self.DIR_TX:
                if neighbor not in self.numCellsToNeighbors:
                    self.numCellsToNeighbors[neighbor]    = 0
                self.numCellsToNeighbors[neighbor]  += len(cellList)
                for neighb in neighbor._myNeigbors():
                    if self!=neighb:
                        self._reserve_cell_neighbor(cellList,neighb)
            else:
                if neighbor not in self.numCellsFromNeighbors:
                    self.numCellsFromNeighbors[neighbor]    = 0
                self.numCellsFromNeighbors[neighbor]  += len(cellList)
                for neighb in self._myNeigbors():
                    if neighbor!=neighb:
                        self._reserve_cell_neighbor(cellList,neighb)
            
            if self.settings.queuing != 0  :
                self.cellsAllocToNeighbor[neighbor] = []
                for (ts,ch,dir) in cellList :
                    self.cellsAllocToNeighbor[neighbor] += [ts]
                self.top_add_response(cells, neighbor, dirNeighbor)#, cells)
            return cells

    def _top_abort_transaction(self):
        with self.dataLock:
            if self.pendingTransaction != None:
                #self.sequenceNumberWithNeighbor[self.pendingTransaction.neighbor] = self.pendingTransaction.sequenceNum
                #if self.pendingTransaction.type == "parentAdds" : #or self.pendingTransaction.type == "confirmation":
                if self.pendingTransaction.cells :
                    cellsToRemove = []
                    dirToRemove = None
                    for (ts, ch, dir) in self.pendingTransaction.cells :
                        dirToRemove = dir
                        cellsToRemove += [ts]
                    if dirToRemove and cellsToRemove :
                        self._tsch_removeCells(self.pendingTransaction.neighbor,cellsToRemove, dirToRemove)
                        #if self.requestTriggered[self.pendingTransaction.neighbor] == True :
                self.requestTriggered[self.pendingTransaction.neighbor] = False
            self.transactionRetries = 0
            self.pendingTransaction = None
            self._incrementMoteStats('transactionAborted')
                
    def top_cell_deletion_sender(self,neighbor,tsList):
        with self.dataLock:
            # log
            self._log(
                self.INFO,
                "[6top] remove timeslots={0} with {1}",
                (tsList,neighbor.id),
            )
            self._tsch_removeCells(
                neighbor     = neighbor,
                tsList       = tsList,
                dir          = self.DIR_TX
            )
            #for ts in tsList :
            #    if ts not in neighbor.schedule :
            #        tsList.remove(ts)
            neighbor.top_cell_deletion_receiver(self,tsList)
            assert self.numCellsToNeighbors[neighbor]>=0

    def top_cell_deletion_receiver(self,neighbor,tsList):
        with self.dataLock:
            cellList=[]
            for ts in tsList :
                cellList +=[(ts,self.schedule.get(ts)['ch'])]
            self._tsch_removeCells(
                neighbor     = neighbor,
                tsList       = tsList,
                dir          = self.DIR_RX
            )
            for neighb in self._myNeigbors():
                if neighbor!=neighb:
                    self._delete_cell_neighbor(cellList,neighb)
                    
            #if neighbor in self.numCellsFromNeighbors :
            #    if self.numCellsFromNeighbors[neighbor] <=0 :
            #        self.numCellsFromNeighbors[neighbor] = 0
            #else :
            #    self.numCellsFromNeighbors[neighbor] = 0

    def _top_removeCells(self,neighbor,numCellsToRemove):
        '''
        Finds cells to neighbor, and remove it.
        '''

        # get cells to the neighbors
        scheduleList = []

        ########## worst cell removing initialized by theoritical pdr ##########
        for ts, cell in self.schedule.iteritems():
            if cell['neighbor']==neighbor and cell['dir']==self.DIR_TX :
                cellPDR=(float(cell['numTxAck'])+(self.getPDR(neighbor)*self.NUM_SUFFICIENT_TX))/(cell['numTx']+self.NUM_SUFFICIENT_TX)
                scheduleList+=[(ts,cell['numTxAck'],cell['numTx'],cellPDR)]

        # introduce randomness in the cell list order
        random.shuffle(scheduleList)

        if not self.settings.noRemoveWorstCell:

            # triggered only when worst cell selection is due (cell list is sorted according to worst cell selection)
            scheduleListByPDR={}
            for tscell in scheduleList:
                if not scheduleListByPDR.has_key(tscell[3]):
                    scheduleListByPDR[tscell[3]]=[]
                scheduleListByPDR[tscell[3]]+=[tscell]
            rssi            = self.getRSSI(neighbor)
            theoPDR         = Topology.Topology.rssiToPdr(rssi)
            scheduleList=[]
            for pdr in sorted(scheduleListByPDR.keys()):
                if pdr<theoPDR:
                    scheduleList+=sorted(scheduleListByPDR[pdr], key=lambda x: x[2], reverse=True)
                else:
                    scheduleList+=sorted(scheduleListByPDR[pdr], key=lambda x: x[2])

        # remove a given number of cells from the list of available cells (picks the first numCellToRemove)
        tsList=[]
        for tscell in scheduleList[:numCellsToRemove]:

            # log
            self._log(
                self.INFO,
                "[otf] remove cell ts={0} to {1} (pdr={2:.3f})",
                (tscell[0],neighbor.id,tscell[3]),
            )
            if self.settings.queuing != 0 and ( tscell[0] not in neighbor.schedule.keys() or neighbor.schedule[tscell[0]]['neighbor'] != self):
                continue
            tsList += [tscell[0]]
        # remove cells
        #if self.pendingTransaction != None and neighbor == self.pendingTransaction.neighbor:
        #    return
        self.top_cell_deletion_sender(neighbor,tsList)

    def _top_isUnusedSlot(self,ts):
        with self.dataLock:
            return not (ts in self.schedule)

    #===== tsch
    def _tsch_enqueueSlotZero(self, packet):
        
        if self.settings.queuing == 2:
                if  packet['data'][4] == "answer":
                    self.controlQueueHP += [packet]
                elif packet['data'][4] == "req":
                    self.controlQueueNP += [packet]
        elif self.settings.queuing == 1 :
            self.controlQueue += [packet]
            
        return True
    
    def _tsch_enqueue(self,packet):
        if not self.preferredParent:
            # I don't have a route

            # increment mote state
            self._incrementMoteStats('droppedNoRoute')
            #print "noroute"
            return False

        elif not self.getTxCells():
            # I don't have any transmit cells

            # increment mote state
            self._incrementMoteStats('droppedNoTxCells')
            #print self
            #print "notxcell"
            return False

        elif packet['type'] == self.APP_TYPE_DATA and len(self.txQueue)==self.TSCH_QUEUE_SIZE:
            # my TX queue is full

            # update mote stats
            self._incrementMoteStats('droppedQueueFull')
            #print "data queue full"
            return False

        elif packet['type'] == self.APP_TYPE_CONTROL and (self.settings.queuing == 1 and len(self.controlQueue)==self.TOP_CQUEUE_SIZE):
                self._incrementMoteStats('droppedQueueFull')
                #print "control queue full"
                return False
        else:
            # all is good

            # enqueue packet
            if self.settings.queuing != 0 and packet['type'] == 'CONTROL' :
                self.controlQueue += [packet]
            else :
                self.txQueue     += [packet]

            return True

    def _tsch_schedule_activeCell(self):

        asn        = self.engine.getAsn()
        tsCurrent  = asn%self.settings.slotframeLength

        # find closest active slot in schedule
        with self.dataLock:

            if not self.schedule:
                #self._log(self.DEBUG,"[tsch] empty schedule")
                self.engine.removeEvent(uniqueTag=(self.id,'activeCell'))
                return
            tsDiff                = 0
            tsDiffMin             = None
            for (ts,cell) in self.schedule.items():
                if ts==tsCurrent:
                    tsDiff        = self.settings.slotframeLength
                elif ts>tsCurrent:
                    tsDiff        = ts-tsCurrent
                elif ts<tsCurrent:
                    tsDiff        = (ts+self.settings.slotframeLength)-tsCurrent
                else:
                    raise SystemError()

                if (not tsDiffMin) or (tsDiffMin>tsDiff):
                    tsDiffMin     = tsDiff

        # schedule at that ASN
        self.engine.scheduleAtAsn(
            asn         = asn+tsDiffMin,
            cb          = self._tsch_action_activeCell,
            args        = None,
            uniqueTag   = (self.id,'activeCell'),
            priority    = 0,
        )

    def _tsch_action_activeCell(self, args=None):
        ''' active slot starts. Determine what todo, either RX or TX, use the propagation model to introduce
            interference and Rx packet drops.
        '''

        #self._log(self.DEBUG,"[tsch] _tsch_action_activeCell")
        asn = self.engine.getAsn()
        ts  = asn%self.settings.slotframeLength

        with self.dataLock:
            
            # make sure this is an active slot
            assert ts in self.schedule

            # make sure we're not in the middle of a TX/RX operation
            assert not self.waitingFor or self.waitingFor == self.DIR_SHARED
            #print self.getTxCells() == []
            listeningZero = False
            cell = self.schedule[ts]
            
            #shared slot : if nothing to send, we read the channel
            
            if cell['dir']==self.DIR_SHARED :
                # TSCH CSMA/CA delay

                if (not self.sendcontrolFailed or (self.sendcontrolFailed and self.sendcontrolDelay == 0)):
                    self.pktToSend = None
                    
                    if self.controlQueue and self.controlQueue[0] != None :
                        self.pktToSend = self.controlQueue[0]
                        #prioritize answer over other kind of control
                        if not(self.pktToSend['data'][4] == "answer") :
                            t = [p for p in self.controlQueue if p['data'][4] == "answer"]
                            if t :
                                self.pktToSend = t[0]
                                
                    if self.pktToSend != None and (self.getTxCells() == [] or (self.pktToSend['data'][4] == "answer") or (not self.settings.opportunist) or (self.settings.opportunist and ((self.pktToSend['dmac'] not in self.numCellsToNeighbors) or (self.pktToSend['dmac'] in self.numCellsToNeighbors and self.numCellsToNeighbors[self.pktToSend['dmac']] == 0) or (self.pktToSend['dmac'] in self.otfStatus and self.otfStatus[self.pktToSend['dmac']] == 'STOP') or (self.pktToSend['dmac'] not in self.otfStatus)))) :
                        self.propagation.startTx(
                            channel   = cell['ch'],
                            type     = self.pktToSend['type'],
                            data      = self.pktToSend['data'],
                            smac      = self,
                            dmac      = self.pktToSend['dmac'],
                            payload   = self.pktToSend['payload'],
                        )
                        
                        self._incrementMoteStats('controlPacketsSent')
                        # log charge usage
                        self._logChargeConsumed(self.CHARGE_TxDataRxAck_uC)
                        
                        self.waitingFor = self.DIR_SHARED
                    elif self.pktToSend != None :
                        if self.pktToSend:
                            self.pktToSendAlloc = self.pktToSend

                        # start listening on the open slot
                        listeningZero = True
                        self.propagation.startRx(
                            mote = self,
                            channel = 0,
                        )
                        self.waitingFor   = self.DIR_SHARED
                    else :
                        # start listening on the open slot
                        listeningZero = True
                        self.propagation.startRx(
                            mote = self,
                            channel = 0,
                        )
                        self.waitingFor   = self.DIR_SHARED
                elif self.sendcontrolFailed :
                    self.sendcontrolDelay -= 1
            elif cell['dir']==self.DIR_RX:
                # start listening
                self.propagation.startRx(
                    mote          = self,
                    channel       = cell['ch'],
                )

                # indicate that we're waiting for the RX operation to finish
                self.waitingFor   = self.DIR_RX

            elif cell['dir']==self.DIR_TX:
                self.pktToSend = None

                if self.settings.opportunist :
                    if self.pktToSendAlloc in self.controlQueue :
                        self.pktToSend = self.pktToSendAlloc
                    
                if not self.pktToSend and self.txQueue :
                    self.pktToSend = self.txQueue[0]
    
                if self.pktToSend:
                    cell['numTx'] += 1

                    self.propagation.startTx(
                        channel   = cell['ch'],
                        type     = self.pktToSend['type'],
                        data      = self.pktToSend['data'],
                        smac      = self,
                        dmac      = cell['neighbor'],
                        payload   = self.pktToSend['payload'],
                    )

                    # indicate that we're waiting for the TX operation to finish
                    self.waitingFor   = self.DIR_TX

                    # log charge usage
                    self._logChargeConsumed(self.CHARGE_TxDataRxAck_uC)

                elif self.settings.queuing != 0 and ts in range(0, self.settings.numSharedSlots):
                    listeningZero = True
                    self.propagation.startRx(
                        mote          = self,
                        channel       = 0,
                    )
                    self.waitingFor   = self.DIR_SHARED
                    
                    # schedule next active cell
                    
            # Goes to listening open slot automatically 
            if self.waitingFor == self.DIR_SHARED and self.settings.queuing != 0 and not listeningZero and ts in range(0, self.settings.numSharedSlots) :
                self.propagation.startRx(
                    mote = self,
                    channel = 0,
                )
                self.waitingFor   = self.DIR_SHARED
            
            self._tsch_schedule_activeCell()

    def _tsch_addCells(self,neighbor,cellList):
        ''' adds cells to the schedule '''

        with self.dataLock:
            for cell in cellList:
                self.schedule[cell[0]] = {
                    'ch':                 cell[1],
                    'dir':                cell[2],
                    'neighbor':           neighbor,
                    'numTx':              0,
                    'numTxAck':           0,
                    'numRx':              0,
                    'history':            [],
                    'rxDetectedCollision':  False,
                    'debug_canbeInterfered':    [], # for debug purpose, shows schedule collision that can be interfered with minRssi or larger level
                    'debug_interference':       [], # for debug purpose, shows an interference packet with minRssi or larger level
                    'debug_lockInterference':   [], # for debug purpose, shows locking on the interference packet
                    'debug_cellCreatedAsn':     self.engine.getAsn(), # for debug purpose
                }
                # log
                self._log(
                    self.INFO,
                    "[tsch] add cell ts={0} ch={1} dir={2} with {3}",
                    (cell[0],cell[1],cell[2],neighbor.id),
                )
            self._tsch_schedule_activeCell()

    def _tsch_removeCells(self,neighbor,tsList, dir = None):
        ''' removes a cell from the schedule '''

        with self.dataLock:
            # log
            self._log(
                self.INFO,
                "[tsch] remove timeslots={0} with {1}",
                (tsList,neighbor.id),
            )
            for ts in tsList:
                if ts in self.schedule.keys() and self.schedule[ts]['neighbor']==neighbor:
                    if dir == self.DIR_TX :
                        self.numCellsToNeighbors[neighbor] -= 1
                    elif dir == self.DIR_RX :
                        self.numCellsFromNeighbors[neighbor] -= 1
                    self.schedule.pop(ts)

            self._tsch_schedule_activeCell()

    #===== radio

    def txDone(self,isACKed,isNACKed):
        '''end of tx slot'''

        asn   = self.engine.getAsn()
        ts    = asn%self.settings.slotframeLength

        with self.dataLock:

            assert ts in self.schedule
            assert self.schedule[ts]['dir']==self.DIR_TX or self.schedule[ts]['dir']==self.DIR_SHARED
            assert self.waitingFor==self.DIR_TX or self.waitingFor==self.DIR_SHARED
            if isACKed:
                # update schedule stats
                self.schedule[ts]['numTxAck'] += 1

                # update history
                self.schedule[ts]['history'] += [1]

                # update queue stats
                self._logQueueDelayStat(asn-self.pktToSend['asn'])

                # time correction
                if self.schedule[ts]['neighbor'] == self.preferredParent:
                    self.timeCorrectedSlot = asn
                # remove packet from queue
                if self.pktToSend['type'] == "CONTROL":
                    self.requestTriggered[self.pktToSend['dmac']] = False
                    if self.settings.queuing == 2:
                        if self.pktToSend in self.controlQueueHP :
                            self.controlQueueHP.remove(self.pktToSend)
                        elif self.pktToSend in self.controlQueueNP :
                            self.controlQueueNP.remove(self.pktToSend)
                    elif self.settings.queuing == 1 :
                        if self.pktToSend in self.controlQueue :
                            self.sendcontrolFailed = False
                            self.controlQueue.remove(self.pktToSend)
                    #if self.pktToSend['data'][4] == "confirmation" :
                    #    self.pendingTransaction = None
                    self.sendcontrolFailed = False
                    self.requestTriggered[self.pktToSend['dmac']] = False
                    self.macBackoffNB = 0
                    self.sendcontrolDelay = 0
                    self.backoffExponent = self.macMinBE
                    
                elif self.pktToSend['type'] == "DATA" :
                    self.txQueue.remove(self.pktToSend)


            elif isNACKed:
                # update schedule stats as if it is successfully tranmitted
                self.schedule[ts]['numTxAck'] += 1

                # update history
                self.schedule[ts]['history'] += [1]

                # time correction
                if self.schedule[ts]['neighbor'] == self.preferredParent:
                    self.timeCorrectedSlot = asn

                # decrement 'retriesLeft' counter associated with that packet
                if self.pktToSend['type'] == "DATA" :
                    i = self.txQueue.index(self.pktToSend)
                    if self.txQueue[i]['retriesLeft'] > 0:
                        self.txQueue[i]['retriesLeft'] -= 1

                    # drop packet if retried too many time
                    if self.txQueue[i]['retriesLeft'] == 0:

                        if  len(self.txQueue) == self.TSCH_QUEUE_SIZE:

                            # update mote stats
                            self._incrementMoteStats('droppedMacRetries')

                            # remove packet from queue
                            self.txQueue.remove(self.pktToSend)

                elif self.pktToSend['type'] == "CONTROL"  and ts in self.sharedSlots :

                    if self.settings.queuing == 1 :
                        # update BE
                        self.macBackoffNB += 1
                        self.backoffExponent = min(self.backoffExponent + 1, self.macMaxBE)
                        rand = random.randint(1, pow(2, self.backoffExponent))
                        self.sendcontrolDelay = random.randint(1, pow(2, self.backoffExponent))
                        self.sendcontrolFailed = True
                        
                        if self.controlQueue :
                            i = self.controlQueue.index(self.pktToSend)
                            if self.controlQueue[i]['retriesLeft'] > 0:
                                self.controlQueue[i]['retriesLeft'] -= 1
                            # drop packet if retried too many time
                            if self.controlQueue[i]['retriesLeft'] == 0 or self.macBackoffNB == self.macMaxCSMABackoffs:

                                self.sendcontrolFailed = False
                                self.requestTriggered[self.pktToSend['dmac']] = False
                                self.macBackoffNB = 0
                                self.sendcontrolDelay = 0
                                self.backoffExponent = self.macMinBE
                                # update mote stats
                                self._incrementMoteStats('droppedMacRetries')

                                # remove packet from queue
                                self.controlQueue.remove(self.pktToSend)
                                self._top_abort_transaction()
                                #if self.pktToSend['dmac'].pendingTransaction != None and  self.pktToSend['dmac'].pendingTransaction.neighbor == self :
                                if self.pktToSend['data'][4] != 'req' :
                                    self.pktToSend['dmac']._top_abort_transaction()

                                
                    elif self.settings.queuing == 2 :
                        if self.pktToSend in self.controlQueueHP :
                            i = self.controlQueueHP.index(self.pktToSend)
                            if self.controlQueueHP[i]['retriesLeft'] > 0:
                                self.controlQueueHP[i]['retriesLeft'] -= 1

                            # drop packet if retried too many time
                            if self.controlQueueHP[i]['retriesLeft'] == 0:

                                if  len(self.controlQueueHP) == self.TSCH_QUEUE_SIZE:

                                    # update mote stats
                                    self._incrementMoteStats('droppedMacRetries')

                                    # remove packet from queue
                                    self.controlQueueHP.remove(self.pktToSend)
                        elif self.pktToSend in self.controlQueueNP :
                            i = self.controlQueueNP.index(self.pktToSend)
                            if self.controlQueueNP[i]['retriesLeft'] > 0:
                                self.controlQueueNP[i]['retriesLeft'] -= 1

                            # drop packet if retried too many time
                            if self.controlQueueNP[i]['retriesLeft'] == 0:

                                if  len(self.controlQueueNP) == self.TSCH_QUEUE_SIZE:

                                    # update mote stats
                                    self._incrementMoteStats('droppedMacRetries')

                                    # remove packet from queue
                                    self.controlQueueNP.remove(self.pktToSend)
                                    
                elif self.pktToSend['type'] == "CONTROL"  and ts not in self.sharedSlots :
                    if self.settings.queuing == 1 :
                        self.sendControlFailed = True
                        if self.controlQueue :
                            i = self.controlQueue.index(self.pktToSend)
                            if self.controlQueue[i]['retriesLeft'] > 0:
                                self.controlQueue[i]['retriesLeft'] -= 1
                            # drop packet if retried too many time
                            if self.controlQueue[i]['retriesLeft'] == 0 :
                                self.sendcontrolFailed = False
                                self.requestTriggered[self.pktToSend['dmac']] = False

                                # update mote stats
                                self._incrementMoteStats('droppedMacRetries')

                                # remove packet from queue
                                self.controlQueue.remove(self.pktToSend)
                                self._top_abort_transaction()
                                #if self.pktToSend['data'][4] == 'answer' :
                                #if self.pktToSend['dmac'].pendingTransaction != None and  self.pktToSend['dmac'].pendingTransaction.neighbor == self :
                                if self.pktToSend['data'][4] != 'req' :
                                    self.pktToSend['dmac']._top_abort_transaction()
                    
                                
                    elif self.settings.queuing == 2 :
                        if self.pktToSend in self.controlQueueHP :
                            i = self.controlQueueHP.index(self.pktToSend)
                            if self.controlQueueHP[i]['retriesLeft'] > 0:
                                self.controlQueueHP[i]['retriesLeft'] -= 1

                            # drop packet if retried too many time
                            if self.controlQueueHP[i]['retriesLeft'] == 0:

                                if  len(self.controlQueueHP) == self.TSCH_QUEUE_SIZE:

                                    # update mote stats
                                    self._incrementMoteStats('droppedMacRetries')

                                    # remove packet from queue
                                    self.controlQueueHP.remove(self.pktToSend)
                        elif self.pktToSend in self.controlQueueNP :
                            i = self.controlQueueNP.index(self.pktToSend)
                            if self.controlQueueNP[i]['retriesLeft'] > 0:
                                self.controlQueueNP[i]['retriesLeft'] -= 1

                            # drop packet if retried too many time
                            if self.controlQueueNP[i]['retriesLeft'] == 0:

                                if  len(self.controlQueueNP) == self.TSCH_QUEUE_SIZE:

                                    # update mote stats
                                    self._incrementMoteStats('droppedMacRetries')

                                    # remove packet from queue
                                    self.controlQueueNP.remove(self.pktToSend)

            else:
                # update history
                self.schedule[ts]['history'] += [0]

                # decrement 'retriesLeft' counter associated with that packet
                if self.pktToSend['type'] == "DATA" :
                    i = self.txQueue.index(self.pktToSend)
                    if self.txQueue[i]['retriesLeft'] > 0:
                        self.txQueue[i]['retriesLeft'] -= 1
                    # drop packet if retried too many time
                    if self.txQueue[i]['retriesLeft'] == 0:

                        if  len(self.txQueue) == self.TSCH_QUEUE_SIZE:

                            # update mote stats
                            self._incrementMoteStats('droppedMacRetries')

                            # remove packet from queue
                            self.txQueue.remove(self.pktToSend)

                elif self.pktToSend['type'] == 'CONTROL' and ts in self.sharedSlots:
                    #print "other shared slot"
                    if self.settings.queuing == 2 :
                        if self.controlQueueHP :
                            i = self.controlQueueHP.index(self.pktToSend)
                            if self.controlQueueHP[i]['retriesLeft'] > 0:
                                self.controlQueueHP[i]['retriesLeft'] -= 1

                            # drop packet if retried too many time
                            if self.controlQueueHP[i]['retriesLeft'] == 0:

                                if  len(self.controlQueueHP) == self.TSCH_QUEUE_SIZE:

                                    # update mote stats
                                    self._incrementMoteStats('droppedMacRetries')

                                    # remove packet from queue
                                    self.controlQueueHP.remove(self.pktToSend)
                        else :
                            i = self.controlQueueNP.index(self.pktToSend)
                            if self.controlQueueNP[i]['retriesLeft'] > 0:
                                self.controlQueueNP[i]['retriesLeft'] -= 1

                            # drop packet if retried too many time
                            if self.controlQueueNP[i]['retriesLeft'] == 0:

                                if  len(self.controlQueueNP) == self.TSCH_QUEUE_SIZE:

                                    # update mote stats
                                    self._incrementMoteStats('droppedMacRetries')

                                    # remove packet from queue
                                    self.controlQueueNP.remove(self.pktToSend)
                                    
                    elif self.settings.queuing == 1 :
                        
                        # update BE
                        self.macBackoffNB += 1
                        self.backoffExponent = min(self.backoffExponent + 1, self.macMaxBE)
                        self.sendcontrolDelay = random.randint(1, pow(2, self.backoffExponent))
                        self.sendcontrolFailed = True

                        #print self.sendcontrolDelay
                        if self.controlQueue :
                            i = self.controlQueue.index(self.pktToSend)
                            if self.controlQueue[i]['retriesLeft'] > 0:
                                self.controlQueue[i]['retriesLeft'] -= 1
                            # drop packet if retried too many time
                            if self.controlQueue[i]['retriesLeft'] == 0 or self.macBackoffNB == self.macMaxCSMABackoffs:
                                self.sendcontrolFailed = False
                                self.requestTriggered[self.pktToSend['dmac']] = False
                                self.macBackoffNB = 0
                                self.sendcontrolDelay = 0
                                self.backoffExponent = self.macMinBE
                                # update mote stats
                                self._incrementMoteStats('droppedMacRetries')

                                # remove packet from queue
                                self.controlQueue.remove(self.pktToSend)
                                self._top_abort_transaction()
                                #if self.pktToSend['dmac'].pendingTransaction != None and self.pktToSend['dmac'].pendingTransaction.neighbor == self :
                                if self.pktToSend['data'][4] != 'req' :
                                    self.pktToSend['dmac']._top_abort_transaction()
                                
                elif self.pktToSend['type'] == 'CONTROL' and ts not in self.sharedSlots:


                    if self.settings.queuing == 2 :
                        if self.controlQueueHP :
                            i = self.controlQueueHP.index(self.pktToSend)
                            if self.controlQueueHP[i]['retriesLeft'] > 0:
                                self.controlQueueHP[i]['retriesLeft'] -= 1

                            # drop packet if retried too many time
                            if self.controlQueueHP[i]['retriesLeft'] == 0:

                                if  len(self.controlQueueHP) == self.TSCH_QUEUE_SIZE:

                                    # update mote stats
                                    self._incrementMoteStats('droppedMacRetries')

                                    # remove packet from queue
                                    self.controlQueueHP.remove(self.pktToSend)
                        else :
                            i = self.controlQueueNP.index(self.pktToSend)
                            if self.controlQueueNP[i]['retriesLeft'] > 0:
                                self.controlQueueNP[i]['retriesLeft'] -= 1

                            # drop packet if retried too many time
                            if self.controlQueueNP[i]['retriesLeft'] == 0:

                                if  len(self.controlQueueNP) == self.TSCH_QUEUE_SIZE:

                                    # update mote stats
                                    self._incrementMoteStats('droppedMacRetries')

                                    # remove packet from queue
                                    self.controlQueueNP.remove(self.pktToSend)
                                    
                    elif self.settings.queuing == 1 :

                        self.sendcontrolFailed = True
                        if self.controlQueue :
                            i = self.controlQueue.index(self.pktToSend)
                            if self.controlQueue[i]['retriesLeft'] > 0:
                                self.controlQueue[i]['retriesLeft'] -= 1
                                
                            # drop packet if retried too many time
                            if self.controlQueue[i]['retriesLeft'] == 0:
                                self.sendcontrolFailed = False
                                self.requestTriggered[self.pktToSend['dmac']] = False
                                # update mote stats
                                self._incrementMoteStats('droppedMacRetries')

                                # remove packet from queue
                                self.controlQueue.remove(self.pktToSend)
                                self._top_abort_transaction()
                                #if self.pktToSend['dmac'].pendingTransaction != None and self.pktToSend['dmac'].pendingTransaction.neighbor == self :
                                if self.pktToSend['data'][4] != 'req' :
                                    self.pktToSend['dmac']._top_abort_transaction()
                                
            if not self.settings.queuing :
                self.waitingFor = None
            else :
                self.waitingFor = self.DIR_SHARED

            # for debug
            ch = self.schedule[ts]['ch']
            rx = self.schedule[ts]['neighbor']
            canbeInterfered = 0
            for mote in self.engine.motes:
                if mote == self:
                    continue
                if ts in mote.schedule and ch == mote.schedule[ts]['ch'] and mote.schedule[ts]['dir'] == self.DIR_TX:
                    if mote.getRSSI(rx)>rx.minRssi:
                        canbeInterfered = 1
            self.schedule[ts]['debug_canbeInterfered'] += [canbeInterfered]


    def rxDone(self,type=None,data=None,smac=None,dmac=None,payload=None):
        '''end of rx slot'''

        asn   = self.engine.getAsn()
        ts    = asn%self.settings.slotframeLength

        with self.dataLock:

            assert ts in self.schedule
            #assert self.schedule[ts]['dir']==self.DIR_RX or self.schedule[ts]['dir']==self.DIR_SHARED
            assert self.waitingFor==self.DIR_RX or self.waitingFor==self.DIR_SHARED
            (isACKed, isNACKed) = (True, False)
        
            if type == "DATA":
                if smac :
                    # I received a packet

                    # log charge usage
                    self._logChargeConsumed(self.CHARGE_RxDataTxAck_uC)

                    # update schedule stats
                    self.schedule[ts]['numRx'] += 1

                    if self.dagRoot:
                        # receiving packet (at DAG root)

                        # update mote stats
                        self._incrementMoteStats('appReachesDagroot')

                        # calculate end-to-end latency
                        self._logLatencyStat(asn-payload[1])

                        # log the number of hops
                        self._logHopsStat(payload[2])

                        (isACKed, isNACKed) = (True, False)
                    else :
                        # relaying packet

                        # count incoming traffic for each node
                        self._otf_incrementIncomingTraffic(smac)

                        # update the number of hops
                        newPayload     = copy.deepcopy(payload)
                        newPayload[2] += 1

                        # create packet
                        relayPacket = {
                            'asn':         asn,
                            'type':       type,
                            'data':        data,
                            'payload':     newPayload,
                            'retriesLeft': self.TSCH_MAXTXRETRIES
                        }


                        # enqueue packet in TSCH queue

                        isEnqueued = self._tsch_enqueue(relayPacket)
                        
                        if isEnqueued:
                        
                            # update mote stats
                            self._incrementMoteStats('appRelayed')

                            (isACKed, isNACKed) = (True, False)

                        else:
                            (isACKed, isNACKed) = (False, True)


                        
            elif type == 'CONTROL' :
                if data[5] not in self.sequenceNumberWithNeighbor :
                    self.sequenceNumberWithNeighbor[data[5]] = 0
                allGood = True
                if data[8] != self.sequenceNumberWithNeighbor[data[5]] + 1 :
                    allGood = False
                self.sequenceNumberWithNeighbor[data[5]] = data[8]
                if self.engine.getAsn() in self.ignorePacket :
                    allGood = False
                
                if dmac == self and allGood:
                    self._incrementMoteStats('controlPacketsReceived')
                    if data[4] == 'req' :
                        assert data[1]
                        self.top_cell_reservation_response(neighbor = data[5], numCells = data[1], dirNeighbor = data[3], args = None, slotUsedByNeighbor = data[6])

                    if data[4] == 'answer' :
                        self.top_new_handle_request_ok(data[0], data[1], data[5], data[3])
                        
                    if data[4] == 'OTF' :
                        self.otfStatus[data[5]] = data[7]
                    (isACKed, isNACKed) = (True, False)

                    if data[4] == "confirmation" :
                        if len(data[0]) == len(self.cellsAllocToNeighbor[data[5]]) :
                            if data[0] != self.cellsAllocToNeighbor[data[5]] :
                                cells = list(set(data[0] + self.cellsAllocToNeighbor[data[5]]))
                                removeSelf = []
                                removeNeighb = []
                                for ts in cells :
                                    if ts in self.schedule.keys() and ts not in data[5].schedule.keys() and self.schedule[ts]['neighbor'] == data[5]:
                                        removeSelf += [ts]
                                    if ts in data[5].schedule.keys() and ts not in self.schedule.keys() and data[5].schedule[ts]['neighbor'] == self:
                                        removeNeighb += [ts]
                                self._tsch_removeCells(data[5],removeSelf,self.DIR_RX)
                                data[5]._tsch_removeCells(self, removeNeighb, data[5].DIR_TX)
                                #elf.numCellsFromNeighbors[neighbor] -= 
                                #ata[5]..numCellsToNeighbors[self] -= len(removeN
                                self.cellsAllocToNeighbor[data[5]] = []
                        self.pendingTransaction = None
                        data[5].pendingTransaction = None
            else:
                # this was an idle listen
                # log charge usage
                self._logChargeConsumed(self.CHARGE_Idle_uC)
                    
                (isACKed, isNACKed) = (False, False)

            if not self.settings.queuing :
                self.waitingFor = None
            else :
                self.waitingFor = self.DIR_SHARED
                
            return isACKed, isNACKed

    def calcTime(self):
        ''' calculate time compared to base time of Dag root '''

        asn   = self.engine.getAsn()

        time   = 0.0
        child  = self
        parent = self.preferredParent

        while(True):
            if not parent :
                parent = self.preferredParent
                break
            duration  = (asn-child.timeCorrectedSlot) * self.settings.slotDuration # in sec
            driftDiff = child.drift - parent.drift # in ppm
            time += driftDiff * duration # in us
            if parent.dagRoot:
                break
            else:
                child  = parent
                parent = child.preferredParent

        return time


    #===== wireless

    def _estimateETX(self,neighbor):

        with self.dataLock:

            # set initial values for numTx and numTxAck assuming PDR is exactly estimated
            pdr                   = self.getPDR(neighbor)
            numTx                 = self.NUM_SUFFICIENT_TX
            numTxAck              = math.floor(pdr*numTx)

            for (_,cell) in self.schedule.items():
                if (cell['neighbor'] == neighbor) and (cell['dir'] == self.DIR_TX):
                    numTx        += cell['numTx']
                    numTxAck     += cell['numTxAck']

            # abort if about to divide by 0
            if not numTxAck:
                return

            # calculate ETX
            etx = float(numTx)/float(numTxAck)

            return etx

    def setPDR(self,neighbor,pdr):
        ''' sets the pdr to that neighbor'''
        with self.dataLock:
            self.PDR[neighbor] = pdr

    def getPDR(self,neighbor):
        ''' returns the pdr to that neighbor'''
        with self.dataLock:
            return self.PDR[neighbor]

    def _myNeigbors(self):
        return [n for n in self.PDR.keys() if self.PDR[n]>0]

    def setRSSI(self,neighbor,rssi):
        ''' sets the RSSI to that neighbor'''
        with self.dataLock:
            self.RSSI[neighbor.id] = rssi

    def getRSSI(self,neighbor):
        ''' returns the RSSI to that neighbor'''
        with self.dataLock:
            if neighbor.id in self.RSSI :
                return self.RSSI[neighbor.id]
            else :
                return 0

    #===== location

    def setLocation(self,x,y):
        with self.dataLock:
            self.x = x
            self.y = y

    def getLocation(self):
        with self.dataLock:
            return (self.x,self.y)

    #==== battery

    def boot(self):
        for i in range(0, self.settings.numSharedSlots) :
            self.schedule[i*int(math.floor(float(self.settings.slotframeLength) / float(self.settings.numSharedSlots)))] = {
                'ch':                 0,
                'dir':                self.DIR_SHARED,
                'neighbor':           None,
                'numTx':              0,
                'numTxAck':           0,
                'numRx':              0,
                'history':            [],
                'rxDetectedCollision':  False,
                'debug_canbeInterfered':    [], # for debug purpose, shows schedule collision that can be interfered with minRssi or larger level
                'debug_interference':       [], # for debug purpose, shows an interference packet with minRssi or larger level
                'debug_lockInterference':   [], # for debug purpose, shows locking on the interference packet
                'debug_cellCreatedAsn':     self.engine.getAsn(), # for debug purpose
            }
            self.sharedSlots += [i*(int)(self.settings.slotframeLength / self.settings.numSharedSlots)]
        if not self.dagRoot:
            self._app_schedule_sendData(init=True)
            if self.settings.numPacketsBurst != None and self.settings.burstTime != None :
                self._app_schedule_enqueueData()
        self._rpl_schedule_sendDIO(init=True)
        self._otf_resetInTraffic()
        self._otf_schedule_housekeeping()
        if not self.settings.noTopHousekeeping:
            self._top_schedule_housekeeping()
        self._tsch_schedule_activeCell()

    def _logChargeConsumed(self,charge):
        with self.dataLock:
            self.chargeConsumed  += charge

    #======================== private =========================================

    #===== getters

    def getTxCells(self):
        with self.dataLock:
            return [(ts,c['ch'],c['neighbor']) for (ts,c) in self.schedule.items() if c['dir']==self.DIR_TX]

    def getRxCells(self):
        with self.dataLock:
            return [(ts,c['ch'],c['neighbor']) for (ts,c) in self.schedule.items() if c['dir']==self.DIR_RX]
        
    #===== stats

    # mote state

    def _resetMoteStats(self):
        with self.dataLock:
            self.motestats = {
                # app
                'appGenerated':            0,   # number of packets app layer generated
                'appRelayed':              0,   # number of packets relayed
                'appReachesDagroot':       0,   # number of packets received at the DAGroot
                'droppedAppFailedEnqueueData': 0,# dropped packets because app failed enqueue them
                'droppedAppFailedEnqueueControl': 0,# dropped packets because app failed enqueue them
                # queue
                'droppedQueueFull':        0,   # dropped packets because queue is full
                # rpl
                'rplTxDIO':                0,   # number of TX'ed DIOs
                'rplRxDIO':                0,   # number of RX'ed DIOs
                'rplChurnPrefParent':      0,   # number of time the mote changes preferred parent
                'rplChurnRank':            0,   # number of time the mote changes rank
                'rplChurnParentSet':       0,   # number of time the mote changes parent set
                'droppedNoRoute':          0,   # packets dropped because no route (no preferred parent)
                # otf
                'droppedNoTxCells':        0,   # packets dropped because no TX cells
                'otfAdd':                  0,   # OTF adds some cells
                'otfRemove':               0,   # OTF removes some cells
                # 6top
                'transactionAborted':       0,
                'controlPacketsSent':      0,
                'controlPacketsReceived':  0,
                'topTxRelocatedCells':     0,   # number of time tx-triggered 6top relocates a single cell
                'topTxRelocatedBundles':   0,   # number of time tx-triggered 6top relocates a bundle
                'topRxRelocatedCells':     0,   # number of time rx-triggered 6top relocates a single cell
                # tsch
                'droppedMacRetries':       0,   # packets dropped because more than TSCH_MAXTXRETRIES MAC retries
            }

    def _incrementMoteStats(self,name):
        with self.dataLock:
            self.motestats[name] += 1

    def getMoteStats(self):

        # gather statistics
        with self.dataLock:
            returnVal = copy.deepcopy(self.motestats)
            returnVal['numTxCells']         = len(self.getTxCells())
            returnVal['numRxCells']         = len(self.getRxCells())
            returnVal['aveQueueDelay']      = self.getAveQueueDelay()
            returnVal['aveLatency']         = self.getAveLatency()
            returnVal['aveHops']            = self.getAveHops()
            returnVal['probableCollisions']    = self.getRadioStats('probableCollisions')
            returnVal['openSlotCollision']  = self.getRadioStats('openSlotCollision')
            returnVal['txQueueFill']        = len(self.txQueue)
            returnVal['chargeConsumed']     = self.chargeConsumed
            returnVal['numTx']              = sum([cell['numTx'] for (_,cell) in self.schedule.items()])

        # reset the statistics
        self._resetMoteStats()
        self._resetQueueStats()
        self._resetLatencyStats()
        self._resetHopsStats()
        self._resetRadioStats()

        return returnVal

    # cell stats

    def getCellStats(self,ts_p,ch_p):
        ''' retrieves cell stats '''
        returnVal = None
        with self.dataLock:
            for (ts,cell) in self.schedule.items():
                if ts==ts_p and cell['ch']==ch_p:
                    returnVal = {
                        'dir':            cell['dir'],
                        'neighbor':       cell['neighbor'].id,
                        'numTx':          cell['numTx'],
                        'numTxAck':       cell['numTxAck'],
                        'numRx':          cell['numRx'],
                    }
                    break
        return returnVal

    # queue stats

    def getAveQueueDelay(self):
        d = self.queuestats['delay']
        return float(sum(d))/len(d) if len(d)>0 else 0

    def _resetQueueStats(self):
        with self.dataLock:
            self.queuestats = {
                'delay':               [],
            }

    def _logQueueDelayStat(self,delay):
        with self.dataLock:
            self.queuestats['delay'] += [delay]

    # latency stats

    def getAveLatency(self):
        with self.dataLock:
            d = self.packetLatencies
            return float(sum(d))/float(len(d)) if len(d)>0 else 0

    def _resetLatencyStats(self):
        with self.dataLock:
            self.packetLatencies = []

    def _logLatencyStat(self,latency):
        with self.dataLock:
            self.packetLatencies += [latency]

    # hops stats

    def getAveHops(self):
        with self.dataLock:
            d = self.packetHops
            return float(sum(d))/float(len(d)) if len(d)>0 else 0

    def _resetHopsStats(self):
        with self.dataLock:
            self.packetHops = []

    def _logHopsStat(self,hops):
        with self.dataLock:
            self.packetHops += [hops]

    # radio stats

    def getRadioStats(self,name):
        return self.radiostats[name]

    def _resetRadioStats(self):
        with self.dataLock:
            self.radiostats = {
                'probableCollisions':      0,   # number of packets that can collide with another packet
                'openSlotCollision' :      0,   # number of packets that collided in open slot
            }

    def incrementRadioStats(self,name):
        with self.dataLock:
            self.radiostats[name] += 1

    #===== log

    def _log(self,severity,template,params=()):

        if   severity==self.DEBUG:
            if not log.isEnabledFor(logging.DEBUG):
                return
            logfunc = log.debug
        elif severity==self.INFO:
            if not log.isEnabledFor(logging.INFO):
                return
            logfunc = log.info
        elif severity==self.WARNING:
            if not log.isEnabledFor(logging.WARNING):
                return
            logfunc = log.warning
        elif severity==self.ERROR:
            if not log.isEnabledFor(logging.ERROR):
                return
            logfunc = log.error
        else:
            raise NotImplementedError()

        output  = []
        output += ['[ASN={0:>6} id={1:>4}] '.format(self.engine.getAsn(),self.id)]
        output += [template.format(*params)]
        output  = ''.join(output)
        logfunc(output)
    ###########################################Ali jawad FAHS###############################
    def _reserve_cell_neighbor(self,cells,neighbor):
        #reserve cells assigned by a neighbor to avoid collision at dedicated cells (LLME) 
        for cell in cells:
            neighbor.reserve[cell[0]][cell[1]]=True

    def _delete_cell_neighbor(self,cells,neighbor):
        #delete cells deleted  by a neighbor 
        for cell in cells:
            neighbor.reserve[cell[0]][cell[1]]=False

    def _choose_channel(self,neighbor,ts):
     #choose a channel according to the reserve table
        k=[]
        for j in range(self.settings.numChans):
             if self.reserve[ts][j]==False:
                if neighbor.reserve[ts][j]==False:
                    k+= [(j)]
        random.shuffle(k)           
        return k[0]         