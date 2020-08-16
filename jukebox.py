#!/usr/bin/python3

import sys, os
# Add relative paths for the directory where the adapter is located as well as the parent
sys.path.append(os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__),'../../base'))

from sofabase import sofabase, adapterbase, configbase
import devices

import requests
import math
import random
from collections import namedtuple
from collections import defaultdict
import xml.etree.ElementTree as et
import time
import json
import asyncio
import aiohttp
import xmltodict
import re

import base64
import logging

from operator import itemgetter
import concurrent.futures

from aiohttp_sse_client import client as sse_client

class jukebox(sofabase):

    class adapter_config(configbase):
    
        def adapter_fields(self):
            self.jukebox_url=self.set_or_default('jukebox_url', mandatory=True)
            self.jukebox_port=self.set_or_default('jukebox_port', default=443)


    class EndpointHealth(devices.EndpointHealth):

        @property            
        def connectivity(self):
            return 'OK'

    class MusicController(devices.MusicController):

        @property            
        def artist(self):
            try:
                return self.nativeObject['nowplaying']['artist']
            except:
                return ""

        @property            
        def title(self):
            try:
                return self.nativeObject['nowplaying']['name']
            except:
                return ""
       
        @property            
        def album(self):
            try:
                return self.nativeObject['nowplaying']['album']
            except:
                return ""
                
        @property            
        def art(self):
            try:
            # TODO - this will be an external link and we should instead ingest and cache that
            # image so that there is no outside dependency for the client
                return self.nativeObject['nowplaying']['art']
            except:
                pass

        @property            
        def url(self):
            try:
                return self.nativeObject['nowplaying']['url']
            except:
                return ""

        @property            
        def playbackState(self):
            try:
                if self.nativeObject['nowplaying']['is_playing']:
                    return 'PLAYING'
            except:
                pass
            
            return 'PAUSED'
            
        @property            
        def linked(self):
            return []

        async def Play(self, correlationToken=''):
            try:
                await self.adapter.jukeboxCommand('play')
                return self.device.Response(correlationToken)
            except:
                self.log.error('!! Error during Play', exc_info=True)
                self.adapter.connect_needed=True
                return None

        async def Pause(self, correlationToken=''):
            try:
                await self.adapter.jukeboxCommand('pause')
                return self.device.Response(correlationToken)
            except:
                self.log.error('!! Error during Pause', exc_info=True)
                self.adapter.connect_needed=True
                return None
                
        async def Stop(self, correlationToken=''):
            try:
                return self.device.Response(correlationToken)
            except:
                self.log.error('!! Error during Stop', exc_info=True)
                self.adapter.connect_needed=True
                return None
                
        async def Skip(self, correlationToken=''):
            try:
                await self.adapter.jukeboxCommand('next')
                return self.device.Response(correlationToken)
            except:
                self.log.error('!! Error during Skip', exc_info=True)
                self.adapter.connect_needed=True
                return None
                
        async def Previous(self, correlationToken=''):
            try:
                return self.device.Response(correlationToken)
            except:
                self.log.error('!! Error during Skip', exc_info=True)
                self.adapter.connect_needed=True
                return None

    class adapterProcess(adapterbase):

        def __init__(self, log=None, loop=None, dataset=None, notify=None, request=None, config=None, **kwargs):
            self.config=config
            self.dataset=dataset
            self.dataset.nativeDevices['player']={}
            self.log=log
            self.notify=notify
            self.polltime=.1
            self.subscriptions=[]
            self.artcache={}
            self.artqueue=[]
            self.connect_needed=True
            self.running=True
            if not loop:
                self.loop = asyncio.new_event_loop()
            else:
                self.loop=loop
                
        async def start(self):
            try:
                self.log.info('.. Starting Jukebox')
                asyncio.create_task(self.startJukeboxConnection())
                self.log.info('.. main loop')
                #await self.pollIfNecessary()
            except:
                self.log.error('Error starting jukebox service',exc_info=True)

        async def jukeboxCommand(self, command):
            
            try:
                async with aiohttp.ClientSession() as client:
                    async with client.get("%s/%s" % (self.config.jukebox_url, command)) as response:
                        result=await response.read()
                        result=result.decode()
                        self.log.info('.. result: %s' % result)
            except:
                self.log.error('!! Error sending command',exc_info=True)
        
        async def startJukeboxConnection(self):
            
            try:
                newdevice={ "jukebox": { "name": "Jukebox", "nowplaying": {} }}
                await self.dataset.ingest({"player": newdevice})
                while self.running==True:
                    try:
                        # This should establish an SSE connection with the Jukebox
                        timeout = aiohttp.ClientTimeout(total=0)
                        async with sse_client.EventSource(self.config.jukebox_url+"/sse", timeout=timeout) as event_source:
                            try:
                                async for event in event_source:
                                    #self.log.info('.. SSE: %s' % event)
                                    try:
                                        data=json.loads(event.data)
                                        if 'nowplaying' in data:
                                            self.log.info('<< %s' % data)
                                            await self.dataset.ingest({"player": { "jukebox": { "nowplaying": data['nowplaying'] }}})
                                    except:
                                        self.log.error('error parsing data', exc_info=True)
                            except ConnectionError:
                                self.log.error('!! error with SSE connection', exc_info=True)
                    except aiohttp.client_exceptions.ClientConnectorCertificateError:
                        self.log.error('!! error - jukebox SSL certificate error')
                    except concurrent.futures._base.TimeoutError:
                        self.log.error('!! error - jukebox SSE timeout')
                    except:
                        self.log.error('!! error starting jukebox SSE connection', exc_info=True)
            except:
                self.log.error('!! Error in Jukebox connection loop', exc_info=True)
        
        async def addSmartDevice(self, path):
            
            try:
                if path.split("/")[1]=="player":
                    deviceid=path.split("/")[2]
                    nativeObject=self.dataset.nativeDevices['player'][deviceid]
                    if 'name' not in nativeObject:
                        self.log.error('No name in %s %s' % (deviceid, nativeObject))
                        return None
                        
                    if nativeObject['name'] not in self.dataset.localDevices:
                        #if 'ZoneGroupTopology' in nativeObject:
                        device=devices.alexaDevice('jukebox/player/%s' % deviceid, nativeObject['name'], displayCategories=["PLAYER"], adapter=self)
                        device.EndpointHealth=jukebox.EndpointHealth(device=device)
                        device.MusicController=jukebox.MusicController(device=device)
                        return self.dataset.newaddDevice(device)
                return None
            except:
                self.log.error('Error defining smart device', exc_info=True)
                return None


        async def virtualImage(self, path, client=None):
            
            try:
                return None

            except concurrent.futures._base.CancelledError:
                self.log.error('Attempt to get art cancelled for %s %s' % (path,url))
                #self.connect_needed=True
                
            except AttributeError:
                self.log.error('Couldnt get art', exc_info=True)
                self.connect_needed=True
                
            except:
                self.log.error('Couldnt get art', exc_info=True)
                #return {'name':playerObject['name'], 'id':playerObject['speaker']['uid'], 'image':""}
                
            return None



if __name__ == '__main__':
    adapter=jukebox(name='jukebox')
    adapter.start()
