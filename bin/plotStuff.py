#!/usr/bin/python
'''
\brief Plots timelines and topology figures from collected simulation data.

\author Thomas Watteyne <watteyne@eecs.berkeley.edu>
\author Kazushi Muraoka <k-muraoka@eecs.berkeley.edu>
\author Nicola Accettura <nicola.accettura@eecs.berkeley.edu>
\author Xavier Vilajosana <xvilajosana@eecs.berkeley.edu>
'''

#============================ adjust path =====================================

#============================ logging =========================================

import logging
class NullHandler(logging.Handler):
    def emit(self, record):
        pass
log = logging.getLogger('plotTimelines')
log.setLevel(logging.ERROR)
log.addHandler(NullHandler())

#============================ imports =========================================

import os
import re
import glob
import sys
import math

import numpy
import scipy
import scipy.stats

import logging.config
import matplotlib.pyplot
import argparse

#============================ defines =========================================

CONFINT = 0.95

#============================ body ============================================

def parseCliOptions():

    parser = argparse.ArgumentParser()

    parser.add_argument( '--elemNames',
        dest       = 'elemNames',
        nargs      = '+',
        type       = str,
        default    = [
            'numTxCells',
        ],
        help       = 'Name of the elements to generate timeline figures for.',
    )

    parser.add_argument('--simDataDir',
        dest       = 'simDataDir',
        type       = str,
        default    = 'simData',
        help       = 'SimData directory.',
    )

    parser.add_argument('--simDataDir2',
        dest       = 'simDataDir2',
        type       = str,
        default    = 'simData2',
        help       = 'SimData directory for the second plot.',
    )

    parser.add_argument('--plotTitle',
        dest       = 'plotTitle',
        type       = str,
        default    = "",
        help       = "plot title",
    )

    parser.add_argument('--plotTitle2',
        dest       = 'plotTitle2',
        type       = str,
        default    = "",
        help       = "second plot title",
    )
    
    options        = parser.parse_args()

    return options.__dict__

def genTimelinePlots(dir,infilename,elemName):

    infilepath     = os.path.join(dir,infilename)
    outfilename    = infilename.split('.')[0]+'_{}.png'.format(elemName)
    outfilepath    = os.path.join(dir,outfilename)

    # print
    print 'Parsing    {0}...'.format(infilename),

    # find colnumelem, colnumcycle, colnumrunNum
    with open(infilepath,'r') as f:
        for line in f:
            if line.startswith('# '):
                elems        = re.sub(' +',' ',line[2:]).split()
                numcols      = len(elems)
                colnumelem   = elems.index(elemName)
                colnumcycle  = elems.index('cycle')
                colnumrunNum = elems.index('runNum')
                break

    assert colnumelem
    assert colnumcycle
    assert colnumrunNum

    # parse data
    valuesPerCycle = {}
    with open(infilepath,'r') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            m = re.search('\s+'.join(['([\.0-9]+)']*numcols),line.strip())
            cycle  = int(m.group(colnumcycle+1))
            runNum = int(m.group(colnumrunNum+1))
            try:
                elem         = float(m.group(colnumelem+1))
            except:
                try:
                    elem     =   int(m.group(colnumelem+1))
                except:
                    elem     =       m.group(colnumelem+1)
            #print 'cycle={0} runNum={1} elem={2}'.format(cycle,runNum,elem)
            if cycle not in valuesPerCycle:
                valuesPerCycle[cycle] = []
            valuesPerCycle[cycle] += [elem]

    # print
    print 'done.'

    # print
    print 'Generating {0}...'.format(outfilename),

    # calculate mean and confidence interval
    meanPerCycle    = {}
    confintPerCycle = {}
    for (k,v) in valuesPerCycle.items():
        a          = 1.0*numpy.array(v)
        n          = len(a)
        se         = scipy.stats.sem(a)
        m          = numpy.mean(a)
        confint    = se * scipy.stats.t._ppf((1+CONFINT)/2., n-1)
        meanPerCycle[k]      = m
        confintPerCycle[k]   = confint

    # plot
    x         = sorted(meanPerCycle.keys())
    y         = [meanPerCycle[k] for k in x]
    yerr      = [confintPerCycle[k] for k in x]
    matplotlib.pyplot.figure()
    matplotlib.pyplot.errorbar(x,y,yerr=yerr)
    matplotlib.pyplot.savefig(outfilepath)
    matplotlib.pyplot.close('all')

    # print
    print 'done.'

def genTimelinePlots2(dir1,dir2,infilename1,infilename2,elemName):

    infilepath1     = os.path.join(dir1,infilename1)
    infilepath2     = os.path.join(dir2,infilename2)
    outfilename    = infilename1.split('.')[0]+'_{}.png'.format(elemName)
    outfilepath    = os.path.join(dir1,outfilename)

    # print
    print 'Parsing    {0}...'.format(infilename1),

    # find colnumelem, colnumcycle, colnumrunNum
    with open(infilepath1,'r') as f:
        for line in f:
            if line.startswith('# '):
                elems        = re.sub(' +',' ',line[2:]).split()
                numcols      = len(elems)
                colnumelem   = elems.index(elemName)
                colnumcycle  = elems.index('cycle')
                colnumrunNum = elems.index('runNum')
                break
        
    assert colnumelem
    assert colnumcycle
    assert colnumrunNum

    with open(infilepath2,'r') as f:
        for line in f:
            if line.startswith('# '):
                elems2        = re.sub(' +',' ',line[2:]).split()
                numcols2      = len(elems2)
                colnumelem2   = elems2.index(elemName)
                colnumcycle2  = elems2.index('cycle')
                colnumrunNum2 = elems2.index('runNum')
                break
        
    assert colnumelem2
    assert colnumcycle2
    assert colnumrunNum2

    # parse data
    valuesPerCycle1 = {}
    with open(infilepath1,'r') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            m = re.search('\s+'.join(['([\.0-9]+)']*numcols),line.strip())
            cycle  = int(m.group(colnumcycle+1))
            runNum = int(m.group(colnumrunNum+1))
            try:
                elem         = float(m.group(colnumelem+1))
            except:
                try:
                    elem     =   int(m.group(colnumelem+1))
                except:
                    elem     =       m.group(colnumelem+1)
            #print 'cycle={0} runNum={1} elem={2}'.format(cycle,runNum,elem)
            if cycle not in valuesPerCycle1:
                valuesPerCycle1[cycle] = []
            valuesPerCycle1[cycle] += [elem]

    
    # print
    print 'done.'

    # print
    print 'Generating {0}...'.format(outfilename),

    # calculate mean and confidence interval
    meanPerCycle1    = {}
    confintPerCycle1 = {}
    for (k,v) in valuesPerCycle1.items():
        a          = 1.0*numpy.array(v)
        n          = len(a)
        se         = scipy.stats.sem(a)
        m          = numpy.mean(a)
        confint    = se * scipy.stats.t._ppf((1+CONFINT)/2., n-1)
        meanPerCycle1[k]      = m
        confintPerCycle1[k]   = confint

    # plot
    x         = sorted(meanPerCycle1.keys())
    y         = [meanPerCycle1[k] for k in x]
    yerr      = [confintPerCycle1[k] for k in x]

    matplotlib.pyplot.figure()
    matplotlib.pyplot.errorbar(x,y,yerr=yerr)

            
    valuesPerCycle2 = {}
    with open(infilepath2,'r') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            m = re.search('\s+'.join(['([\.0-9]+)']*numcols),line.strip())
            cycle  = int(m.group(colnumcycle2+1))
            runNum = int(m.group(colnumrunNum2+1))
            try:
                elem         = float(m.group(colnumelem2+1))
            except:
                try:
                    elem     =   int(m.group(colnumelem2+1))
                except:
                    elem     =       m.group(colnumelem2+1)
            #print 'cycle={0} runNum={1} elem={2}'.format(cycle,runNum,elem)
            if cycle not in valuesPerCycle2:
                valuesPerCycle2[cycle] = []
            valuesPerCycle2[cycle] += [elem]

    meanPerCycle2    = {}
    confintPerCycle2 = {}
    for (k,v) in valuesPerCycle2.items():
        a          = 1.0*numpy.array(v)
        n          = len(a)
        se         = scipy.stats.sem(a)
        m          = numpy.mean(a)
        confint    = se * scipy.stats.t._ppf((1+CONFINT)/2., n-1)
        meanPerCycle2[k]      = m
        confintPerCycle2[k]   = confint

    
    x2         = sorted(meanPerCycle2.keys())
    y2         = [meanPerCycle2[k] for k in x2]
    yerr2      = [confintPerCycle2[k] for k in x2]
    
    matplotlib.pyplot.errorbar(x2,y2,yerr=yerr2, c = matplotlib.cm.spring(0))

    #red_patch = matplotlib.patches.Patch(color='red', label='The red data', fmt = '^')
    #spring_patch = matplotlib.patches.Patch(color=matplotlib.cm.spring(0), label='the pring data')

    options            = parseCliOptions()
    
    pink_line = matplotlib.patches.Patch(color=matplotlib.cm.spring(0), label=options['plotTitle2'])
    blue_line = matplotlib.patches.Patch(label=options['plotTitle'])

    
    matplotlib.pyplot.legend(handles=[blue_line, pink_line], bbox_to_anchor=(0., 1.02, 1., .102), loc=3, ncol=2, mode="expand", borderaxespad=0.)
    
    matplotlib.pyplot.savefig(outfilepath)
    matplotlib.pyplot.close('all')

    # print
    print 'done.'

    
def genSingleRunTimelinePlots(dir,infilename):

    infilepath     = os.path.join(dir,infilename)

    # print
    print 'Parsing    {0}...'.format(infilename),

    # find colnames
    with open(infilepath,'r') as f:
        for line in f:
            if line.startswith('# '):
                colnames = re.sub(' +',' ',line[2:]).split()
                break

    # data = {
    #    'col1': [
    #       [1,2,3,4,5,6,7],
    #       [1,2,3,4,5,6,7],
    #       ...
    #    ],
    #    'col2': [
    #       [1,2,3,4,5,6,7],
    #       [1,2,3,4,5,6,7],
    #       ...
    #    ],
    #    ...
    # }

    # fill data
    data = {}
    with open(infilepath,'r') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue

            lineelems = re.sub(' +',' ',line[2:]).split()
            line = dict([(colnames[i],lineelems[i]) for i in range(len(lineelems))])

            for k in line.keys():
                try:
                    line[k] = int(line[k])
                except ValueError:
                    line[k] = float(line[k])

            runNum = line['runNum']
            cycle  = line['cycle']

            for (k,v) in line.items():
                if k in ['runNum','cycle']:
                    continue

                if k not in data:
                    data[k] = []

                if runNum==len(data[k]):
                    data[k].append([])

                assert runNum==len(data[k])-1
                assert cycle==len(data[k][runNum])

                data[k][runNum] += [v]
    # verify data integrity
    numRuns = None
    for v in data.values():
        if numRuns==None:
            numRuns = len(v)
        else:
            assert len(v)==numRuns
    numCycles = None
    for v in data.values():
        for r in v:
            if numCycles==None:
                numCycles = len(r)
            else:
                assert len(r)==numCycles

    # print
    print 'done.'

    # plot timelines
    for runNum in range(numRuns):

        outfilename = 'run_{}_timelines.png'.format(runNum)
        outfilepath = os.path.join(dir,outfilename)

        # print
        print 'Generating {0}...'.format(outfilename),

        #=== start new plot

        matplotlib.rc('font', size=6)
        fig = matplotlib.pyplot.figure(
            figsize     = (8,70),
            dpi         = 80,
        )
        fig.subplots_adjust(hspace=0.5)

        #=== plot data

        firstax = None

        for (row,title) in enumerate(sorted(data.keys())):
            if not firstax:
                ax = fig.add_subplot(len(data),1,row)
                firstax = ax
            else:
                ax = fig.add_subplot(len(data),1,row,sharex=firstax)
                matplotlib.pyplot.setp( ax.get_xticklabels(), visible=True)
            ax.set_ylabel(title)
            ax.set_xlabel("Cycle")
            ax.set_title(title + " evolution in time")
            ax.plot(data[title][runNum])

        #=== save and close plot

        matplotlib.pyplot.savefig(outfilepath)
        matplotlib.pyplot.close('all')

        # print
        print 'done.'

def genTopologyPlots(dir,infilename):

    infilepath     = os.path.join(dir,infilename)

    # print
    print "TOPOLOGY PLOT"
    print 'Parsing    {0}...'.format(infilename),

    # parse data
    xcoord         = {}
    ycoord         = {}
    motes          = {}
    links          = {}
    with open(infilepath,'r') as f:
        for line in f:
            if line.startswith('##'):
                # squareSide
                m = re.search('squareSide\s+=\s+([\.0-9]+)',line)
                if m:
                    squareSide    = float(m.group(1))

                # minRssi
                m = re.search('minRssi\s+=\s+([-0-9]+)',line)
                if m:
                    minRssi       = int(m.group(1))

            if line.startswith('#pos'):

                # runNum
                m = re.search('runNum=([0-9]+)',line)
                runNum = int(m.group(1))

                # initialize variables
                motes[runNum]     = []
                xcoord[runNum]    = {}
                ycoord[runNum]    = {}

                # motes
                m = re.findall('([0-9]+)\@\(([\.0-9]+),([\.0-9]+)\)\@([0-9]+)',line)
                for (id,x,y,rank) in m:
                    id       = int(id)
                    x        = float(x)
                    y        = float(y)
                    rank     = int(rank)
                    motes[runNum] += [{
                        'id':     id,
                        'x':      x,
                        'y':      y,
                        'rank':   rank,
                    }]
                    xcoord[runNum][id] = x
                    ycoord[runNum][id] = y

            if line.startswith('#links'):

                # runNum
                m = re.search('runNum=([0-9]+)',line)
                runNum = int(m.group(1))

                # create entry for this run
                links[runNum] = []

                # links
                m = re.findall('([0-9]+)-([0-9]+)@([\-0-9]+)dBm@([\.0-9]+)',line)
                for (moteA,moteB,rssi,pdr) in m:
                    links[runNum] += [{
                        'moteA':  int(moteA),
                        'moteB':  int(moteB),
                        'rssi':   int(rssi),
                        'pdr':    float(pdr),
                    }]

    # verify integrity
    assert squareSide
    assert sorted(motes.keys())==sorted(links.keys())

    # print
    print 'done.'

    def plotMotes(thisax):
        # motes
        thisax.scatter(
            [mote['x'] for mote in motes[runNum] if mote['id']!=0],
            [mote['y'] for mote in motes[runNum] if mote['id']!=0],
            marker      = 'o',
            c           = 'white',
            s           = 10,
            lw          = 0.5,
            zorder      = 4,
        )
        thisax.scatter(
            [mote['x'] for mote in motes[runNum] if mote['id']==0],
            [mote['y'] for mote in motes[runNum] if mote['id']==0],
            marker      = 'o',
            c           = 'red',
            s           = 10,
            lw          = 0.5,
            zorder      = 4,
        )

    # plot topologies
    for runNum in sorted(motes.keys()):

        outfilename = 'run_{}_topology.png'.format(runNum)
        outfilepath = os.path.join(dir,outfilename)

        # print
        print 'Generating {0}...'.format(outfilename),

        #=== start new plot

        matplotlib.rc('font', size=8)
        fig = matplotlib.pyplot.figure(
            figsize     = (8,12),
            dpi         = 80,
        )

        #=== plot 1: motes and IDs

        ax1 = fig.add_subplot(3,2,1,aspect='equal')
        ax1.set_title('mote positions and IDs')
        ax1.set_xlabel('x (km)')
        ax1.set_ylabel('y (km)')
        ax1.set_xlim(xmin=0,xmax=squareSide)
        ax1.set_ylim(ymin=0,ymax=squareSide)

        # motes
        plotMotes(ax1)

        # id
        for mote in motes[runNum]:
            ax1.annotate(
                mote['id'],
                xy      = (mote['x']+0.01,mote['y']+0.01),
                color   = 'blue',
                size    = '6',
                zorder  = 3,
            )

        #=== plot 2: contour

        ax2 = fig.add_subplot(3,2,2,aspect='equal')
        ax2.set_title('RPL rank contour')
        ax2.set_xlabel('x (km)')
        ax2.set_ylabel('y (km)')
        ax2.set_xlim(xmin=0,xmax=squareSide)
        ax2.set_ylim(ymin=0,ymax=squareSide)

        # motes
        #plotMotes(ax2)

        # rank
        '''
        for mote in motes[runNum]:
            ax2.annotate(
                mote['rank'],
                xy      = (mote['x']+0.01,mote['y']-0.02),
                color   = 'black',
                size    = '6',
                zorder  = 2,
            )
        '''

        # contour
        x     = [mote['x']    for mote in motes[runNum]]
        y     = [mote['y']    for mote in motes[runNum]]
        z     = [mote['rank'] for mote in motes[runNum]]

        xi    = numpy.linspace(0,squareSide,100)
        yi    = numpy.linspace(0,squareSide,100)
        zi    = matplotlib.mlab.griddata(x,y,z,xi,yi)

        ax2.contour(xi,yi,zi,lw=0.1)

        #=== plot 3: links

        ax3 = fig.add_subplot(3,2,3,aspect='equal')
        ax3.set_title('connectivity (PDR)')
        ax3.set_xlabel('x (km)')
        ax3.set_ylabel('y (km)')
        ax3.set_xlim(xmin=0,xmax=squareSide)
        ax3.set_ylim(ymin=0,ymax=squareSide)

        # motes
        plotMotes(ax3)

        # links
        cmap       = matplotlib.pyplot.get_cmap('jet')
        cNorm      = matplotlib.colors.Normalize(
            vmin   = min([link['pdr'] for link in links[runNum]]),
            vmax   = max([link['pdr'] for link in links[runNum]]),
        )
        scalarMap  = matplotlib.cm.ScalarMappable(norm=cNorm, cmap=cmap)

        for link in links[runNum]:
            colorVal = scalarMap.to_rgba(link['pdr'])
            ax3.plot(
                [xcoord[runNum][link['moteA']],xcoord[runNum][link['moteB']]],
                [ycoord[runNum][link['moteA']],ycoord[runNum][link['moteB']]],
                color   = colorVal,
                zorder  = 1,
                lw      = 0.3,
            )

        #=== plot 4: TODO

        ax4 = fig.add_subplot(3,2,4)
        ax4.set_xlim(xmin=0,xmax=1)

        pdrs = [link['pdr'] for link in links[runNum]]

        for pdr in pdrs:
            assert pdr>=0
            assert pdr<=1

        ax4.set_title('PDRs ({0} links)'.format(len(pdrs)))
        ax4.hist(pdrs)

        #=== plot 5: rssi vs. distance

        ax5 = fig.add_subplot(3,2,5)
        ax5.set_xlim(xmin=0,xmax=1000*squareSide)
        ax5.set_xlabel('distance (m)')
        ax5.set_ylabel('RSSI (dBm)')

        data_x          = []
        data_y          = []

        pos             = {}
        for mote in motes[runNum]:
            pos[mote['id']] = (mote['x'],mote['y'])

        for link in links[runNum]:
            distance    = 1000*math.sqrt(
                (pos[link['moteA']][0] - pos[link['moteB']][0])**2 +
                (pos[link['moteA']][1] - pos[link['moteB']][1])**2
            )
            rssi        = link['rssi']

            data_x     += [distance]
            data_y     += [rssi]

        ax5.scatter(
            data_x,
            data_y,
            marker      = '+',
            c           = 'blue',
            s           = 3,
            zorder      = 1,
        )

        ax5.plot(
            [0,1000*squareSide],
            [minRssi,minRssi],
            color       = 'red',
            zorder      = 2,
            lw          = 0.5,
        )

        #=== plot 6: waterfall (pdr vs rssi)

        ax6 = fig.add_subplot(3,2,6)
        ax6.set_ylim(ymin=-0.100,ymax=1.100)
        ax6.set_xlabel('RSSI (dBm)')
        ax6.set_ylabel('PDR')

        data_x          = []
        data_y          = []

        for link in links[runNum]:
            data_x     += [link['rssi']]
            data_y     += [link['pdr']]

        ax6.scatter(
            data_x,
            data_y,
            marker      = '+',
            c           = 'blue',
            s           = 3,
        )

        #=== save and close plot

        matplotlib.pyplot.savefig(outfilepath)
        matplotlib.pyplot.close('all')

        # print
        print 'done.'


#============================ main ============================================

def main():

    # initialize logging
    logging.config.fileConfig('logging.conf')

    # parse CLI options
    options            = parseCliOptions()
    simDataDir         = options['simDataDir']
    simDataDir2        = options['simDataDir2']

    # verify there is some data to plot
    if not os.path.isdir(simDataDir):
        print 'There are no simulation results to analyze.'
        sys.exit(1)
    
    # plot figures
    dir = simDataDir
    dir2 = simDataDir2
    if os.path.isdir(dir2) :
        for infilename1, infilename2 in zip(glob.glob(os.path.join(dir,'*.dat')), glob.glob(os.path.join(dir2,'*.dat'))):
            print os.path.join(simDataDir, dir,'*.dat')
            print os.path.join(simDataDir2, dir2,'*.dat')
            # plot timelines
            for elemName in options['elemNames']:
                genTimelinePlots2(
                    dir1          = dir,
                    dir2          = dir2,
                    infilename1   = os.path.basename(infilename1),
                    infilename2   = os.path.basename(infilename2),
                    elemName      = elemName,
                )
            # plot timelines for each run
            genSingleRunTimelinePlots(
                dir               = dir,
                infilename        = os.path.basename(infilename1),
            )
            
            # plot topologies
            genTopologyPlots(
                dir               = dir,
                infilename        = os.path.basename(infilename1),
            )
    else :
        for infilename in glob.glob(os.path.join(dir,'*.dat')):
            print os.path.join(simDataDir, dir,'*.dat')
            # plot timelines
            for elemName in options['elemNames']:
                genTimelinePlots(
                    dir           = dir,
                    infilename    = os.path.basename(infilename),
                    elemName      = elemName,
                )
            # plot timelines for each run
            genSingleRunTimelinePlots(
                dir               = dir,
                infilename        = os.path.basename(infilename),
            )
            
            # plot topologies
            genTopologyPlots(
                dir               = dir,
                infilename        = os.path.basename(infilename),
            )



if __name__=="__main__":
    main()
