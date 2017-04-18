Updates on The 6TiSCH Simulator
====================

Work Done: Ali Jawad Fahs

this GitHub is a clone of my Gitlab at Lig Drakkar.  

Scope
-----

6TiSCH is an active IETF standardization working group which defines mechanisms to build and maintain communication schedules in tomorrow's Internet of (Important) Things. This simulator allows you to measure the performance of those different mechanisms under different conditions.

What is simulated:

* protocols
    * IEEE802.15.4e-2012 TSCH (http://standards.ieee.org/getieee802/download/802.15.4e-2012.pdf)
    * RPL (http://tools.ietf.org/html/rfc6550)
    * 6top (http://tools.ietf.org/html/draft-wang-6tisch-6top-sublayer)
    * On-The-Fly scheduling (http://tools.ietf.org/html/draft-dujovne-6tisch-on-the-fly)
* the "Pister-hack" propagation model with collisions
* the energy consumption model taken from
    * [A Realistic Energy Consumption Model for TSCH Networks](http://ieeexplore.ieee.org/xpl/login.jsp?tp=&arnumber=6627960&url=http%3A%2F%2Fieeexplore.ieee.org%2Fiel7%2F7361%2F4427201%2F06627960.pdf%3Farnumber%3D6627960). Xavier Vilajosana, Qin Wang, Fabien Chraim, Thomas Watteyne, Tengfei Chang, Kris Pister. IEEE Sensors, Vol. 14, No. 2, February 2014.




Gallery
-------

|  |  |  |
|--|--|--|
| ![](https://projectfollowup.000webhostapp.com/images/final.jpg) | ![](https://projectfollowup.000webhostapp.com/images/consistency.jpg) | ![](https://projectfollowup.000webhostapp.com/images/all2.jpg) |

Updates done: 
-------------

* added the functionality of sending the control messages in the channel. 
* added our approaches and implemented llme Ideal and using the value of Pdr. 
* implemented cell buffer for the cells are allocated newly to broadcast them later. 
* debbugging for some error in the code (the original developer where informed and approved the bug).


