#!/usr/bin/python
'''
\brief Wireless propagation model.

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
log = logging.getLogger('Propagation')
log.setLevel(logging.DEBUG)
log.addHandler(NullHandler())

#============================ imports =========================================

import threading
import random
import math

import Topology
import SimSettings
import SimEngine

#============================ defines =========================================

#============================ body ============================================

class Propagation(object):
    
    #===== start singleton
    _instance      = None
    _init          = False
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(Propagation,cls).__new__(cls, *args, **kwargs)
        return cls._instance
    #===== end singleton
    
    def __init__(self):
        
        #===== start singleton
        # don't re-initialize an instance (needed because singleton)
        if self._init:
            return
        self._init = True
        #===== end singleton
        
        # store params
        self.settings                  = SimSettings.SimSettings()
        self.engine                    = SimEngine.SimEngine()
        
        # variables
        self.dataLock                  = threading.Lock()
        self.receivers                 = [] # motes with radios currently on listening
        self.transmissions             = [] # ongoing transmissions
        
        # schedule propagation task
        self._schedule_propagate()
    
    def destroy(self):
        self._instance                 = None
        self._init                     = False
    
    #======================== public ==========================================
    
    #===== communication
    
    def startRx(self,mote,channel):
        ''' add a mote as listener on a channel'''
        with self.dataLock:
            self.receivers += [{
                'mote':                mote,
                'channel':             channel,
            }]
    
    def startTx(self,channel,type, data,smac,dmac,payload):
        ''' add a mote as using a ch. for tx'''
        with self.dataLock:
            self.transmissions  += [{
                'channel':             channel,
                'type':                type,
                'data':                data,
                'smac':                smac,
                'dmac':                dmac,
                'payload':             payload,
            }]
    def isSharedSlotIdle(self, requester):
        talkers = [t['smac'] for t in self.transmissions if (t['smac']!=requester) and (t['channel']== 0)]
        if talkers :
            return False
        else :
            return True
    def propagate(self, args=None):
        ''' Simulate the propagation of pkts in a slot. '''
        
        with self.dataLock:
            
            asn   = self.engine.getAsn()
            ts    = asn%self.settings.slotframeLength
            
            arrivalTime = {}
            # store arrival times of transmission packets

            ans = [t for t in self.transmissions if t['data'] and t['data'][4] == "answer"]
            other = [t for t in self.transmissions if t not in ans]

            # Prioritize answers over requests
            if ans :
                self.transmissions = ans + other

            for transmission in self.transmissions:
                arrivalTime[transmission['smac']] = transmission['smac'].calcTime()
                        
            for transmission in self.transmissions:
                
                i           = 0
                isACKed     = False
                isNACKed    = False
                
                while i<len(self.receivers):
                    
                    if self.receivers[i]['channel']==transmission['channel']:
                        # this receiver is listening on the right channel
                        
                        if self.receivers[i]['mote']==transmission['dmac']:
                            # this packet is destined for this mote
                                                                                      
                            if not self.settings.noInterference:
  
                                #''' ============= for evaluation with interference=================
                                 
                                # other transmissions on the same channel?
                                interferers = [t['smac'] for t in self.transmissions if (t!=transmission) and (t['channel']==transmission['channel'])]
                                
                                # for debug
                                interferenceFlag = 0
                                for itfr in interferers:
                                    if transmission['dmac'].getRSSI(itfr)>transmission['dmac'].minRssi :
                                        interferenceFlag = 1
                                transmission['smac'].schedule[ts]['debug_interference'] += [interferenceFlag]
                                if interferenceFlag:
                                    transmission['smac'].incrementRadioStats('probableCollisions')
                                lockOn = transmission['smac']
                                for itfr in interferers:
                                    if arrivalTime[itfr] < arrivalTime[lockOn] and transmission['dmac'].getRSSI(itfr)>transmission['dmac'].minRssi:
                                        # lock on interference
                                        lockOn = itfr
                                
                                if lockOn == transmission['smac']:
                                    # for debug
                                    transmission['smac'].schedule[ts]['debug_lockInterference'] += [0]
                                    
                                    # calculate pdr, including interference
                                    sinr  = self._computeSINR(transmission['smac'],transmission['dmac'],interferers)
                                    pdr   = self._computePdrFromSINR(sinr, transmission['dmac'])
                                    
                                    # pick a random number
                                    failure = random.random()                                    
                                    if pdr>=failure:
                                        # packet is received correctly                                        
                                        # this mote is delivered the packet
                                        isACKed, isNACKed = self.receivers[i]['mote'].rxDone(
                                            type       = transmission['type'],
                                            data       = transmission['data'],
                                            smac       = transmission['smac'],
                                            dmac       = transmission['dmac'],
                                            payload    = transmission['payload']
                                        )                                        
                                        # this mote stops listening
                                        del self.receivers[i]
                                        
                                    else:
                                        # packet is NOT received correctly
                                        self.receivers[i]['mote'].rxDone()
                                        del self.receivers[i]
                                    
                                else:
                                    # fail due to locking on interference
                                    
                                    # for debug
                                    transmission['smac'].schedule[ts]['debug_lockInterference'] += [1]
                                    
                                    # receive the interference as if it's a desired packet
                                    interferers.remove(lockOn)
                                    pseudo_interferers = interferers + [transmission['smac']]
                                    
                                    # calculate SINR where locked interference and other signals are considered S and I+N respectively
                                    pseudo_sinr  = self._computeSINR(lockOn,transmission['dmac'],pseudo_interferers)
                                    pseudo_pdr   = self._computePdrFromSINR(pseudo_sinr, transmission['dmac'])
                                    
                                    # pick a random number
                                    failure = random.random()
                                    if pseudo_pdr>=failure:
                                        # success to receive the interference and realize collision
                                        transmission['dmac'].schedule[ts]['rxDetectedCollision'] = True
                                    
                                    # desired packet is not received
                                    self.receivers[i]['mote'].rxDone()
                                    del self.receivers[i]
                                                                                                            
                            else:
                                # ============= for evaluation without interference=================
                                interferers = []
                                
                                # for debug
                                transmission['smac'].schedule[ts]['debug_interference'] += [0]
                                transmission['smac'].schedule[ts]['debug_lockInterference'] += [0]
                                
                                # calculate pdr with no interference
                                sinr  = self._computeSINR(transmission['smac'],transmission['dmac'],interferers)
                                pdr   = self._computePdrFromSINR(sinr, transmission['dmac'])
                                
                                # pick a random number
                                failure = random.random()
                                
                                if pdr>=failure:
                                    # packet is received correctly
                                    
                                    # this mote is delivered the packet
                                    isACKed, isNACKed = self.receivers[i]['mote'].rxDone(
                                        type       = transmission['type'],
                                        data       = transmission['data'],
                                        smac       = transmission['smac'],
                                        dmac       = transmission['dmac'],
                                        payload    = transmission['payload']
                                    )
                                    
                                    # this mote stops listening
                                    del self.receivers[i]
                                    
                                else:
                                    # packet is NOT received correctly
                                    self.receivers[i]['mote'].rxDone()
                                    del self.receivers[i]
                            
                        else:
                            # this packet is NOT destined for this mote
                            
                            # move to the next receiver
                            i += 1
                    
                    else:
                        # this receiver is NOT listening on the right channel
                        
                        # move to the next receiver
                        i += 1
                
                # indicate to source packet was sent
                transmission['smac'].txDone(isACKed, isNACKed)
            
            
            # remaining receivers that does not receive a desired packet
            for r in self.receivers:

                if not self.settings.noInterference:
                    
                    interferers = [t['smac'] for t in self.transmissions if t['dmac']!=r['mote'] and t['channel']==r['channel']]
                    
                    lockOn = None
                    for itfr in interferers:
                        
                        if not lockOn:
                            if r['mote'].getRSSI(itfr)>r['mote'].minRssi:
                                lockOn = itfr
                        else:
                            if r['mote'].getRSSI(itfr)>r['mote'].minRssi and arrivalTime[itfr]<arrivalTime[lockOn]:
                                lockOn = itfr


                    if lockOn:
                        # pdr calculation

                        # receive the interference as if it's a desired packet
                        interferers.remove(lockOn)

                        # calculate SINR where locked interference and other signals are considered S and I+N respectively
                        pseudo_sinr  = self._computeSINR(lockOn,r['mote'],interferers)
                        pseudo_pdr   = self._computePdrFromSINR(pseudo_sinr,r['mote'])

                        # pick a random number
                        failure = random.random()
                        if pseudo_pdr>=failure:
                            # success to receive the interference and realize collision
                            r['mote'].schedule[ts]['rxDetectedCollision'] = True
                            #if ts == 0 :
                            #    transmission['smac'].incrementRadioStats('openSlotCollision')

                
                # desired packet is not received
                r['mote'].rxDone()

            # clear all outstanding transmissions
            self.transmissions              = []
            self.receivers                  = []
        self._schedule_propagate()

    #======================== private =========================================

    def _schedule_propagate(self):
        with self.dataLock:
            self.engine.scheduleAtAsn(
                asn         = self.engine.getAsn()+1,
                cb          = self.propagate,
                args        = None,
                uniqueTag   = (None,'propagation'),
                priority    = 1,
            )

    def _computeSINR(self,source,destination,interferers):
        ''' compute SINR  '''

        noise = self._dBmTomW(destination.noisepower)
        # S = RSSI - N
        signal = self._dBmTomW(source.getRSSI(destination)) - noise
        if signal < 0.0:
            # RSSI has not to be below noise level. If this happens, return very low SINR (-10.0dB)
            return -10.0

        totalInterference = 0.0
        for interferer in interferers:
            # I = RSSI - N

            interference = self._dBmTomW(interferer.getRSSI(destination)) - noise
            if interference < 0.0:
                # RSSI has not to be below noise level. If this happens, set interference 0.0
                interference = 0.0
            totalInterference += interference

        sinr = signal/(totalInterference + noise)

        return self._mWTodBm(sinr)

    def _computePdrFromSINR(self, sinr, destination):
        ''' compute PDR from SINR  '''

        equivalentRSSI  = self._mWTodBm(
            self._dBmTomW(sinr+destination.noisepower) + self._dBmTomW(destination.noisepower)
        )

        pdr             = Topology.Topology.rssiToPdr(equivalentRSSI)

        return pdr

    def _dBmTomW(self, dBm):
        ''' translate dBm to mW '''
        return math.pow(10.0, dBm/10.0)
    
    def _mWTodBm(self, mW):
        ''' translate dBm to mW '''
        return 10*math.log10(mW)
