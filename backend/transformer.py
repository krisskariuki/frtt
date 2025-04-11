from config import TARGET_MULTIPLIERS,TIME_FRAMES
from sseclient import SSEClient
from utils import colors,main_thread
from datetime import datetime
from flask import Flask,Response,request
from flask_cors import CORS
from waitress import serve
from queue import Queue
import json
import sys
import time
import threading
import argparse

parser=argparse.ArgumentParser(description='define config parameters like port and url')
parser.add_argument('--broadcast_port',default=8000)
parser.add_argument('--host')
parser.add_argument('--source_port',default=8010)
parser.add_argument('--source_url',default='/mozzart/aviator/stream')

args=parser.parse_args()

app=Flask(__name__)
CORS(app)

class Transformer:
    def __init__(self):
        
        self.lock=threading.Lock()
        self.clients=set()
        
        self.recv_record={
            'round_id':None,
            'unix_time':int(datetime.now().timestamp()),
            'std_time':datetime.now().isoformat(sep=' ',timespec='seconds'),
            'multiplier':1.00
        }

        self.record_table={f'{timeframe}:{target}':{
            'std_time':datetime.now().isoformat(sep=' ',timespec='seconds'),
            'unix_time':int(datetime.now().timestamp()),
            'open':0,
            'high':float('-inf'),
            'low':float('inf'),
            'close':0,
            'close_prev':0,
            'momentum':0,
            'ema_9':0,
            'ema_20':0,
            'ema_50':0,
            'ema_200':0,
            'signal':None
        } for timeframe in TIME_FRAMES for target in TARGET_MULTIPLIERS}

        self.series_table={f'{timeframe}:{target}':[] for timeframe in TIME_FRAMES for target in TARGET_MULTIPLIERS}

    def connect(self,sse_url):
        def run_connect():
            max_retries=5
            retries=0
            retry_delay=5

            while retries < max_retries:
                try:
                    client=SSEClient(sse_url)
                    first_item_received=next(client)
                    print(f'\n\n{colors.green}connected to: {colors.cyan}{sse_url}\n')

                    with self.lock:
                        self.recv_record=json.loads(item.data)

                    for item in client:
                        with self.lock:
                            self.recv_record=json.loads(item.data)
                    break

                except Exception as E:
                    retries+=1
                    print(f'\n{colors.red}connection error!\n{colors.yellow}failed to connect to:{sse_url}.\n{colors.yellow}Reason:{E}')

                    if retries < max_retries:
                        print(f'{colors.magenta}[{retries}] Retrying in {retry_delay} seconds...\n')
                        time.sleep(retry_delay)
                    
                    else:
                        print(f'{colors.red}Max retries reached.Aborting.')
                        sys.exit(1)

        threading.Thread(target=run_connect,daemon=True).start()
    
    def broadcast(self):
                
        @app.route('/market/latest')
        def get_latest():
            target=request.args.get('target',type=str)
            timeframe=request.args.get('timeframe',type=str)

            key=f'{timeframe}:{target}'
            with self.lock:
                record=self.record_table[key]

            return Response(json.dumps(record,indent=True),mimetype='application/json')
        
        
        @app.route('/market/history')
        def get_history():
            target=request.args.get('target',type=str)
            timeframe=request.args.get('timeframe',type=str)

            key=f'{timeframe}:{target}'
            with self.lock:
                series=self.series_table[key]
            
            return Response(json.dumps(series,indent=True),mimetype='application/json')
        
        @app.route('/market/stream')
        def stream_data():
            target=request.args.get('target',type=str)
            timeframe=request.args.get('timeframe',type=str)

            key=f'{timeframe}:{target}'
            with self.lock:
                record=self.record_table[key]

                def event_stream():
                    nonlocal record
                    queue=Queue()

                    with self.lock:
                        self.clients.add((key,queue))

                    if record:
                        yield f"data:{json.dumps(record,separators=(',',':'))}\n\n"

                    while True:
                        record=queue.get()
                        yield f"data:{json.dumps(record,separators=(',',':'))}\n\n"

                return Response(event_stream(),mimetype='text/event-stream')

        def run_app():
            print(f'transformer server is running on {colors.cyan}http://{args.host}:{args.broadcast_port}\n')
            serve(app,host='0.0.0.0',port=args.broadcast_port,channel_timeout=300,threads=50,connection_limit=500)

        threading.Thread(target=run_app,daemon=True).start()

    def transform(self,time_frames,target_multipliers):
        local_record,last_signal=None,None

        def is_time_to_update(last_reset_time,time_frame):
            time_unit,time_step=time_frame.split('_')
            time_step=int(time_step)

            elapsed_time=time.time()-last_reset_time

            time_table={
                'second':time_step,
                'minute':time_step*60,
                'hour':time_step*3600,
                'day':time_step*86400
            }

            return elapsed_time >= time_table.get(time_unit,0)
        
        def get_ema(current_close,previous_close,time_frame,lookback=20):
            time_unit,_=time_frame.split('_')
            
            rounds_per_minute=2.5
            time_multipliers={'second':1/60,'minute':1,'hour':60,'day':1440}

            period=lookback*rounds_per_minute*time_multipliers.get(time_unit)
            alpha=2/(period+1)
            ema=current_close*alpha+(1-alpha)*previous_close

            return ema
        
        def get_signal(ema_9,ema_20,ema_50,ema_200):
            nonlocal last_signal
            signal=None
            if ema_9>ema_20>ema_50>ema_200:
                signal='BUY LONG'
            elif ema_9>ema_20>ema_50:
                signal='BUY'
            elif ema_9<ema_20<ema_50<ema_200:
                signal='SELL LONG'
            elif ema_9<ema_20<ema_50:
                signal='SELL SHORT'

            if last_signal!=signal:
                last_signal=signal

            return signal

        
        def update_metrics(record,target,time_frame):
            key=f'{time_frame}:{target}'
            target=float(target)

            multiplier=record['multiplier']

            with self.lock:
                entry=self.record_table.get(key)

            Std_time,Unix_time,Open,High,Low,Close,Close_prev,Momentum,Ema_9_prev,Ema_20_prev,Ema_50_prev,Ema_200_prev,Signal=entry['std_time'],entry['unix_time'],entry['open'],entry['high'],entry['low'],entry['close'],entry['close_prev'],entry['momentum'],entry['ema_9'],entry['ema_20'],entry['ema_50'],entry['ema_200'],entry['signal']

            Close+=(target-1) if multiplier>target else -1
            High=max(Open,High,Close)
            Low=min(Open,Low,Close)
        
            Ema_9=get_ema(Close,Ema_9_prev,time_frame,9)
            Ema_20=get_ema(Close,Ema_20_prev,time_frame,20)
            Ema_50=get_ema(Close,Ema_50_prev,time_frame,50)
            Ema_200=get_ema(Close,Ema_200_prev,time_frame,200)


            if is_time_to_update(Unix_time,time_frame):
                Signal=get_signal(Ema_9,Ema_20,Ema_50,Ema_200)
                Momentum=Close-Close_prev

                self.series_table[key].append(
                {'std_time':Std_time,'unix_time':Unix_time,'open':Open,'high':High,'low':Low,'signal':Signal,'close':Close,'close_prev':Close_prev,'momentum':Momentum,'ema_9':round(Ema_9,2),'ema_20':round(Ema_20,2),'ema_50':round(Ema_50,2),'ema_200':round(Ema_200,2)}
                )
                
                Std_time=datetime.now().isoformat(sep=' ',timespec='seconds')
                Unix_time=int(datetime.now().timestamp())
                Close_prev=Close
                Open=Close
                High=Close
                Low=Close
                
            record={
                'std_time':Std_time,'unix_time':Unix_time,'open':Open,'high':High,'low':Low,'signal':Signal,'close':Close,'close_prev':Close_prev,'momentum':Momentum,'ema_9':round(Ema_9,2),'ema_20':round(Ema_20,2),'ema_50':round(Ema_50,2),'ema_200':round(Ema_200,2)}
            
            with self.lock:
                for client_key,client_queue in list(self.clients):
                    if client_key==key:
                        try:
                            client_queue.put(record)
                        except:
                            self.clients.remove((client_key,client_queue))

                self.record_table[key]=record

        def run_transformer():
            nonlocal local_record
            while True:
                if local_record is None or local_record['round_id']!=self.recv_record['round_id']:
                    local_record=self.recv_record
                    
                    for timeframe in time_frames:
                        for target in target_multipliers:
                            update_metrics(local_record,target,timeframe)

                time.sleep(1)
        
        threading.Thread(target=run_transformer,daemon=True).start()

tf=Transformer()
tf.connect(f'http://{args.source_host}:{args.source_port}/{args.source_url}')
tf.transform(TIME_FRAMES,TARGET_MULTIPLIERS)
tf.broadcast()

main_thread()
