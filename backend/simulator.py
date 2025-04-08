from flask import Flask,Response,jsonify
from flask_cors import CORS
from datetime import datetime
from waitress import serve
from queue import Queue
from config import LOCAL_IP,PRODUCER_PORT
from utils import colors,main_thread
import time
import json
import pandas as pd
import threading
import argparse

parser=argparse.ArgumentParser(description='simulate historic data using')
parser.add_argument('source',type=str)
parser.add_argument('--unalive',action='store_false')
parser.add_argument('--constant',action='store_true')
parser.add_argument('--speed',type=float,default=20)
parser_args=parser.parse_args()

app = Flask(__name__)
CORS(app)

class Simulator:
    def __init__(self,filepath,run_live=True):
        self.run_live=run_live
        self.record={}
        self.series=[]
        self.record_lock=threading.Lock()
        self.series_lock=threading.Lock()
        self.clients=set()

        self.source=pd.read_csv(filepath).to_dict(orient='split')['data']
        
        threading.Thread(target=self.yield_data,daemon=True).start()
    
    def start_server(self):
        @app.route('/simulation/latest',methods=['GET'])
        def get_latest():
            with self.record_lock:
                return jsonify(self.record)

        @app.route('/simulation/history',methods=['GET'])
        def get_history():
            with self.series_lock:
                return jsonify(self.series)

        @app.route('/simulation/stream')
        def stream_data():
            def event_stream():
                queue=Queue()
                self.clients.add(queue)

                with self.record_lock:
                    if self.record:
                        yield f"data:{json.dumps(self.record,separators=(',',':'))}\n\n"

                while True:
                    record=queue.get()
                    yield f"data:{json.dumps(record,separators=(',',':'))}\n\n"
                    time.sleep(1)
            return Response(event_stream(),mimetype='text/event-stream')
        
        def run_server():
            print(f'\n{colors.green}Started simulation\n{colors.white}server is running on {colors.cyan}http://{LOCAL_IP}:{PRODUCER_PORT}\n')
            serve(app,host='0.0.0.0',port=PRODUCER_PORT,channel_timeout=300,threads=50,connection_limit=500)
        
        threading.Thread(target=run_server,daemon=True).start()

    def yield_data(self):
        i=0
        sleep_time=0

        while True:
            recv_record=self.source[i]
            round_id=recv_record[0]
            multiplier=recv_record[1]
            std_time=datetime.now().isoformat(sep=' ',timespec='seconds') if self.run_live else recv_record[2]
            unix_time=int(datetime.now().timestamp()) if self.run_live else recv_record[3]

            constant_time=parser_args.speed
            acceleration_time=parser_args.speed*multiplier**0.2

            sleep_time=constant_time if parser_args.constant else acceleration_time
            
            for client in list(self.clients):
                try:
                    client.put(self.record)
                except:
                    self.clients.remove(client)
            
            time.sleep(sleep_time)
                
            with self.record_lock,self.series_lock:
                self.record={'round_id':round_id,'multiplier':multiplier,'std_time':std_time,'unix_time':unix_time}
                self.series.append(self.record)
            
            print(f'{colors.grey}round_id:{round_id} | std_time:{std_time} | multiplier:{multiplier}')

            i+=1
            if i>=len(self.source):

                print(f'{colors.yellow}End of simulation!')
                break

simulator=Simulator(filepath=parser_args.source,run_live=parser_args.unalive)
simulator.start_server()

main_thread()