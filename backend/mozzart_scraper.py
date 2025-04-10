from scrape_tools import *
from utils import colors,main_thread
from datetime import datetime
from flask import Flask,jsonify,request,Response
from flask_cors import CORS
from queue import Queue
from waitress import serve
from config import DEBUG,LOCAL_IP,MOZZART_URL,MOZZART_BROADCAST_PORT,MOZZART_ACCOUNT_PORT
import pandas as pd 
import threading
import time
import os
import random
import json
import argparse

parser=argparse.ArgumentParser(description='configuration parameters for mozzart scraper')
parser.add_argument('--backup',action='store_true')
parser_args=parser.parse_args()

app=Flask(__name__)
CORS(app)

class MozzartBroadcaster:
    def __init__(self,window_size=(800,1080),wait_time=20,retries=4,backup=False):
        self.window_size=window_size
        self.wait_time=wait_time
        self.retries=retries
        self.short_delay=lambda:time.sleep(random.uniform(1,3))
        self.medium_delay=lambda:time.sleep(random.uniform(4,6))
        self.long_delay=lambda:time.sleep(random.uniform(7,9))

        self.url=MOZZART_URL
        self.headless=not DEBUG
        self.port=MOZZART_BROADCAST_PORT

        self.lock=threading.Lock()

        self.round_id=0
        self.filename=None
        self.backup=backup
        self.folder_name='mozzart'
        self.base_filename='file'
        self.record=None
        self.series=[]

        self.clients=set()
        
    def start_driver(self):
        options=uc.ChromeOptions()
        width,height=self.window_size

        headmode_args=['--ignore-certificate-errors','--disable-notifications']

        headless_args=[
        "--headless=new",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-extensions",
        "--disable-popup-blocking",
        ]+headmode_args
        
        args=headless_args if self.headless else headmode_args
        
        for arg in args:
            options.add_argument(arg)

        self.driver=uc.Chrome(options=options,version_main=134)
        self.driver.maximize_window() if self.window_size==(0,0) else self.driver.set_window_size(width,height)
        stealth(driver=self.driver,platform='Win32',fix_hairline=True)

        self.driver.get(self.url)
        print(f'{colors.cyan}navigating to {self.url}...')
    
    def restart_driver(self):
        try:
            self.driver.quit()
        except:
            pass
        time.sleep(1)
        self.start_driver()
    
    def manage_backup(self):
        os.makedirs(self.folder_name,exist_ok=True)

        if self.filename is None:
            fileid=datetime.now().strftime('%Y%m%d_%H%M')
            self.filename=f"{self.folder_name}/{self.base_filename}_{fileid}.csv"

            if not os.path.exists(self.filename):
                pd.DataFrame(columns=['self.round_id', 'multiplier', 'std_time', 'unix_time']).to_csv(self.filename,index=False)
                print(f'{colors.green}backup file initialized: {self.filename}')

    def save_record(self):
        with self.lock:
            if self.record and isinstance(self.record,dict):
                pd.DataFrame([self.record]).to_csv(self.filename,mode='a',index=False,header=False)
    
    def login(self,phone,password):
        self.start_driver()
        print(f'{colors.cyan}logging in...')
        WebDriverWait(self.driver,self.wait_time).until(EC.element_to_be_clickable((By.XPATH,'//a[@class="login-link mozzart_ke"]'))).click()

        self.short_delay()
        print(f'{colors.cyan}writing phone...')
        WebDriverWait(self.driver,self.wait_time).until(EC.element_to_be_clickable((By.XPATH,'//input[@placeholder="Mobile number"]'))).send_keys(phone)
        

        self.short_delay()
        print(f'{colors.cyan}writing password...')
        WebDriverWait(self.driver,self.wait_time).until(EC.element_to_be_clickable((By.XPATH,'//input[@placeholder="Password"]'))).send_keys(password)
        
        self.short_delay()
        print(f'{colors.cyan}submitting...')
        WebDriverWait(self.driver,self.wait_time).until(EC.element_to_be_clickable((By.XPATH,'//button[@class="login-button"]'))).click()

        
        self.medium_delay()
        print(f'{colors.cyan}connecting to game engine...')
        WebDriverWait(self.driver,self.wait_time).until(EC.element_to_be_clickable((By.XPATH,'//img[@alt="Aviator"]'))).click()

    def broadcast_aviator(self):      
        @app.route('/mozzart/aviator/latest',methods=['GET'])
        def get_latest():
            with self.lock:
                return jsonify(self.record)

        @app.route('/mozzart/aviator/history',methods=['GET'])
        def get_history():
            with self.lock:
                return jsonify(self.series)

        @app.route('/mozzart/aviator/stream',methods=['GET'])
        def stream_data():
            def event_stream():
                queue=Queue()
                self.clients.add(queue)

                with self.lock:
                    if self.record:
                        yield f"data:{json.dumps(self.record,separators=(',',':'))}\n\n"
                
                while True:
                    record=queue.get()
                    yield f"data:{json.dumps(record,separators=(',',':'))}\n\n"
                    time.sleep(1)
            return Response(event_stream(),mimetype='text/event-stream')
        
        def start_server():
            serve(app,host='0.0.0.0',port=self.port,channel_timeout=300,threads=50,backlog=1000,connection_limit=500)
        
        threading.Thread(target=start_server,daemon=True).start()

    def watch_aviator(self):

        old_multiplier=None

        def check_for_new_data(recent_multiplier):
            nonlocal old_multiplier

            if old_multiplier!=recent_multiplier:
                    old_multiplier=recent_multiplier
                    self.round_id+=1
                    multiplier=float(recent_multiplier[0].text.replace('x','').replace(',',''))
                    std_time=datetime.now().isoformat(sep=' ',timespec='seconds')
                    unix_time=int(datetime.now().timestamp())

                    data={'round_id':self.round_id,'multiplier':multiplier,'std_time':std_time,'unix_time':unix_time}
                    with self.lock:
                        self.record=data
                        self.series.append(data)
                        
                    if self.filename:
                        self.save_record()
                    
                    for client in list(self.clients):
                        try:
                            client.put(self.record)
                        except:
                            self.clients.remove(client)
                    if DEBUG:
                        print(f'{colors.grey}round_id: {self.round_id} | std_time: {std_time} | multiplier: {multiplier}')
        
        def run_aviator():
            try:
                payouts_block=WebDriverWait(self.driver,self.wait_time).until(EC.presence_of_element_located((By.XPATH,'//*[@class="payouts-block"]')))
                if payouts_block:
                    if self.backup:
                        self.manage_backup()

                    self.broadcast_aviator()
                    print(f'{colors.grey}server running at {colors.cyan}http://{LOCAL_IP}:{MOZZART_BROADCAST_PORT}/betika/aviator/stream|latest|history')
                    
                while True:
                    try:
                        latest_multipliers=self.driver.find_element(By.CLASS_NAME,'payouts-block').find_elements(By.XPATH,'//div[@class="payout ng-star-inserted" and @appcoloredmultiplier]')
                        check_for_new_data(latest_multipliers)
                    except:
                        raise

                    time.sleep(1)

            except Exception as e:
                print(f'{colors.red}game engine error!\n{colors.yellow}{e}')
            
        threading.Thread(target=run_aviator,daemon=True).start()


class MozzartAccount(MozzartBroadcaster):
    def __init__(self,phone,password,wait_time=20,retries=4,window_size=(800,1080)):
        self.account_id=hex(int(phone))[2:6]
        self.phone=phone
        self.password=password
        self.wait_time=wait_time
        self.retries=retries
        self.window_size=window_size

        self.headless=DEBUG
        self.port=MOZZART_ACCOUNT_PORT

        super().__init__(wait_time=wait_time,retries=retries)

        self.short_delay=lambda:time.sleep(random.uniform(1,3))
        self.lock=threading.Lock()

        self.balance=0.00
        self.target_multiplier=1.01
        self.bet_amount=5.01

        self.record={}
        self.series=[]

        self.trade_activity=None
        self.start_trade=False
        self.stop_trade=False
    
    def broadcast_account(self):
        @app.route(f'/mozzart/account/{self.account_id}/latest',methods=['GET'])
        def get_latest():
            with self.lock:
                return jsonify(self.record)

        @app.route(f'/mozzart/account/{self.account_id}/history',methods=['GET'])
        def get_history():
            with self.lock:
                return jsonify(self.series)

        @app.route(f'/mozzart/account/{self.account_id}/stream',methods=['GET'])
        def stream_data():
            local_record=None

            def event_stream():
                nonlocal local_record
                
                while True:
                    with self.lock:
                        if local_record!=self.record:
                            yield f"data:{json.dumps(self.record,separators=(',',':'))}\n\n"

                    time.sleep(1)

            return Response(event_stream(),mimetype='text/event-stream')
        
        def start_server():
            serve(app,host='0.0.0.0',port=self.port,channel_timeout=300,threads=50,backlog=1000,connection_limit=500)
        
        threading.Thread(target=start_server,daemon=True).start()

        print(f'started account server for {self.phone}')
    def watch_account(self):
        super().login(self.phone,self.password)

        def start_trade():
            autobet_panel=WebDriverWait(self.driver,self.wait_time).until(EC.presence_of_element_located((By.XPATH,'//button[normalize-space(text())="Auto"]')))
            autocashout_start_button=WebDriverWait(self.driver,self.wait_time).until(EC.presence_of_element_located((By.XPATH,'//div[@class="cash-out-switcher"]//div[@class="input-switch off"]')))
            amount_input,multiplier_input=WebDriverWait(self.driver,self.wait_time).until(EC.presence_of_all_elements_located((By.XPATH,'//*[@inputmode="decimal"]')))
            autobet_start_button=WebDriverWait(self.driver,self.wait_time).until(EC.presence_of_element_located((By.XPATH,'//div[@class="auto-bet"]//div[@class="input-switch off"]')))

            autobet_panel.click()

            self.short_delay()
            autocashout_start_button.click()

            self.short_delay()
            multiplier_value=multiplier_input.get_attribute('value')
            for _ in range(len(multiplier_value.split())):
                multiplier_input.send_keys(Keys.CONTROL,Keys.BACKSPACE)
            
            multiplier_input.send_keys(self.target_multiplier)


            self.short_delay()
            amount_value=amount_input.get_attribute('value')
            for _ in range(len(amount_value.split())):
                amount_input.send_keys(Keys.CONTROL,Keys.BACKSPACE)

            amount_input.send_keys(self.bet_amount)

            self.short_delay()
            autobet_start_button.click()

            self.start_trade=False
        
        def stop_trade():
            autocashout_stop_button=WebDriverWait(self.driver,self.wait_time).until(EC.presence_of_element_located((By.XPATH,'//div[@class="cash-out-switcher"]//div[@class="input-switch"]')))
            autobet_stop_button=WebDriverWait(self.driver,self.wait_time).until(EC.presence_of_element_located((By.XPATH,'//div[@class="auto-bet"]//div[@class="input-switch"]')))

            autobet_stop_button.click()
            self.stop_trade=False
                
        def start_stop_trade():
            if self.start_trade and self.trade_activity=='start':
                try:
                    start_trade()
                except:
                    start_trade()
            
            elif self.stop_trade and self.trade_activity=='stop':
                try:
                    stop_trade()
                except:
                    stop_trade()

        def track_balance(recent_balance):
            account_balance=float(recent_balance.text)

            if account_balance!=self.balance:
                with self.lock:
                    self.balance=account_balance

                std_time=datetime.now().isoformat(sep=' ',timespec='seconds')
                unix_time=int(datetime.now().timestamp())

                with self.lock:
                    self.record={'std_time':std_time,'unix_time':unix_time,'balance':self.balance}
                    self.series.append(self.record)
                
                if DEBUG:
                    print(f'time:{std_time} | balance: {self.balance}')
        
        def run_account():
            try:
                payouts_block=WebDriverWait(self.driver,self.wait_time).until(EC.presence_of_element_located((By.XPATH,'//*[@class="payouts-block"]')))
                manualbet_panel=WebDriverWait(self.driver,self.wait_time).until(EC.presence_of_element_located((By.XPATH,'//button[normalize-space(text())="Bet"]')))

                if payouts_block:
                    print(f'{colors.green}connected to game engine successfully')
                    self.broadcast_account()
                
                while True:
                    latest_balance=WebDriverWait(self.driver,self.wait_time).until(EC.presence_of_element_located((By.XPATH,'//div[contains(@class,"balance")]//span[contains(@class,"amount")]')))

                    track_balance(latest_balance)
                    start_stop_trade()

                    time.sleep(1)

            except Exception as E:
                print(f'{colors.red}account error! {E}')
        
        threading.Thread(target=run_account,daemon=True).start()


mb=MozzartBroadcaster(backup=parser_args.backup)
mb.login('0117199001','Chri570ph3r.')
mb.watch_aviator()

main_thread()
