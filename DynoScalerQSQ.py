# ---------------------------------
# SCRIPT AUTOSCALING DYNOS
# RELEASE v.1.0

import os
import json
import boto3
import shutil
import time
import requests
import base64
import datetime
from botocore.exceptions import ClientError
from elasticsearch import Elasticsearch
from apscheduler.schedulers.blocking import BlockingScheduler
import requests

class DynoScaler:
    """
    This class tracks the amount of SQS messages from an AWS account and the amount of active Dynos from a Heroku application,
    with these two values 'DynoScaler' can estimate the amount of active Dynos required to keep a particular metric called 
    'BackLogPerInstance' under certain threshold. 'BackLogPerInstance' is the average amount of SQS messages that have to be 
    processed by each active Dyno at some point. The name of the target metric and its funtional concept comes from this AWS Docs:
        
        https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-using-sqs-queue.html

    The objetive of 'DynoScaler' is to increase or decrease the amount of active Dynos to control the 'BackLogPerInstance' metric,
    the fromula is:

        BackLogPerInstance = AmountOfSQSMessages/ActiveDynos

    DynoScaler working concept:
    'scale_up_cnt' and 'scale_down_cnt' are two varibles inside of 'DynoScaler' class that control the scale up and down action with conjunction
    with 'DYNOSCALER_DOWN_CYCLES' and 'DYNOSCALER_DOWN_CYCLES' through the cycles execution. Each cycle 'scale_up_cnt' or 'scale_down_cnt'
    increase or decrease its value depending on 'BackLogPerInstance' metric.
    
        ScalingUP:
            If the 'BackLogPerInstance' is over 'DYNOSCALER_BACKLOG_THRESHOLD' 'scale_up_cnt' will increase  ist value in 
            one unit and 'scale_down_cnt' will decrease the same amount, when 'scale_up_cnt' reach 'DYNOSCALER_UP_CYCLES' one Dyno will be added. 

        ScalingDOWN:   
            For the opposite case wher if the 'BackLogPerInstance' is under 'DYNOSCALER_BACKLOG_THRESHOLD' 'scale_down_cnt' will increase one unit and 
            'scale_up_cnt' will decrease the same amount, finally when 'scale_down_cnt' reach 'DYNOSCALER_DOWN_CYCLES' a Dyno will be removed.

    Using DynoScaler:
    The simpliest way to use 'DynoScaler', is defining all Evironment Variables required and excecut the apscheduler process.
    To provide autorization, a new authorization has to be added, to do this just refer to:

        https://dashboard.heroku.com/account/applications

        1.  Click in 'Create authorization' and fill the 'Description' box, set 'Expires after' box and click in 'Create'

        2.  Setting the Environmetal Variables:
            [Heroku]
            MY_HEROKU_AUTH_DES     :   Description of the 'Created authorization' in the previous step. 
            MY_HEROKU_AUTH_TOKEN   :   Token of the 'Created authorization' in the previous step. 
            MY_HEROKU_AS_APP       :   The name of the app to control the amount of dynos.
            MY_HEROKU_AS_PROCESS   :   The type of process deployed in Heroku, i.e clock, worker. 

            [AWS]
            AWS_KEY         :   The AWS key name (aws_access_key_id)
            AWS_SECRET      :   The AWS secret acces key (aws_secret_access_key)
            AWS_SQS_NAME    :   The name of the tracked SQS.
            AWS_SQS_REGION  :   The region of AWS here SQS y deployed.
            
            [DynoScaler]
            DYNOSCALER_MAX_DYNOS            :   Maximun number of dynos.
            DYNOSCALER_MIN_DYNOS            :   Minimun number of dynos.
            DYNOSCALER_UP_CYCLES            :   Acumulated cycles required to scale up.
            DYNOSCALER_DOWN_CYCLES          :   Acumulated cycles required to scale down.
            DYNOSCALER_BACKLOG_THRESHOLD    :   SQS messages expected to be handled by each dyno.

            [Elastisearch]
            ELK_ELASTIC_URL     :   Elasticsearch's End point.
            ELK_ELASTIC_USER    :   User of Elasticsearch.
            ELK_ELASTIC_PASS    :   Password.
            ELK_INDEX_AS        :   Index where all information generated will be storaged.
            ELK_INDEX_TYPE_AS   :   Type of index.

    Considetation:
        *   This class tracks only SQS visible messages (ApproximateNumberOfMessagesVisible) from CloudFront.
        *   If any ElaticSearch varibles (ELK_<Varible>) are not provided, logs (dictionary with information) will not be indexed
            and logs will be displayed in the terminal.
        *   This Class is still volatil. In future versions the amount of Dynos will be set with more stable algorith.

    Author:
        Ing. Mec. Juan Camilo Cática
    """
    def __init__(self):
        self._active_dynos = 0
        self._required_dynos = 0
        self._sqs_messages = 0
        self._scale_up_cnt = 0
        self._scale_down_cnt = 0
        self._status_ok = False

    def read_ens(self):
        """
        This method reads the whole enviromental varieble required.
        """
        try:
            # ----------------------------------------
            # ENVIRONMENT VARIABLES HEROKU
            self._HEROKU_AUTH_DES = os.environ['MY_HEROKU_AUTH_DES']
            self._HEROKU_AUTH_TOKEN = os.environ['MY_HEROKU_AUTH_TOKEN']
            self._HEROKU_AS_APP = os.environ['MY_HEROKU_AS_APP']
            self._HEROKU_AS_PROCESS = os.environ['MY_HEROKU_AS_PROCESS']

            # ----------------------------------------
            # ENVIRONMENT VARIABLES HEROKU
            self._DYNOSCALER_MAX_DYNOS = int(os.environ['DYNOSCALER_MAX_DYNOS'])
            self._DYNOSCALER_MIN_DYNOS = int(os.environ['DYNOSCALER_MIN_DYNOS'])
            self._DYNOSCALER_UP_CYCLES = int(os.environ['DYNOSCALER_UP_CYCLES'])
            self._DYNOSCALER_DOWN_CYCLES = int(os.environ['DYNOSCALER_DOWN_CYCLES'])
            self._DYNOSCALER_BACKLOG_THRESHOLD = float(os.environ['DYNOSCALER_BACKLOG_THRESHOLD'])

            if self._DYNOSCALER_UP_CYCLES < 1 and self._DYNOSCALER_DOWN_CYCLES < 1:
                raise Exception("Bad cycles","You must set CYCLES greater than 1 !")

            if self._DYNOSCALER_MAX_DYNOS < 1 and self._DYNOSCALER_MIN_DYNOS < 1 and self._DYNOSCALER_MAX_DYNOS < self._DYNOSCALER_MIN_DYNOS:
                raise Exception("Bad min/max","You must set min/max correctly !")

            # ----------------------------------------
            # HEADER | Generate Base64 encoded API Key
            accept = "application/vnd.heroku+json; version=3"
            auth = base64.b64encode("{}:{}".format(self._HEROKU_AUTH_DES,self._HEROKU_AUTH_TOKEN).encode("UTF-8")) 
            self._headers = dict(Accept=accept, Authorization=auth)

            # ----------------------------------------
            # ENVIRONMENT VARIABLES AWS
            self._AWS_KEY = os.environ['AWS_KEY']
            self._AWS_SECRET = os.environ['AWS_SECRET']
            self._AWS_SQS_NAME = os.environ['AWS_SQS_NAME']
            self._AWS_SQS_REGION = os.environ['AWS_SQS_REGION']

            # ----------------------------------------
            # SETTING THE INITIAL AMOUNT OF DYNOS
            # @IMPORTANT
            self._active_dynos = self._DYNOSCALER_MIN_DYNOS
            self._required_dynos = self._DYNOSCALER_MIN_DYNOS

            # ----------------------------------------
            # DynoScaler Status
            self._status_ok = True
        except Exception as e:
            self._elk_ok = False
            print("ENVs failure :(")
            print(e)
            return

        try:
            # ----------------------------------------
            # ENVIRONMENT VARIABLES ELK
            self._ELK_ELASTIC_URL = os.environ['ELK_ELASTIC_URL']
            self._ELK_ELASTIC_USER = os.environ['ELK_ELASTIC_USER']
            self._ELK_ELASTIC_PASS = os.environ['ELK_ELASTIC_PASS']
            self._ELK_INDEX_AS = os.environ['ELK_INDEX_AS']
            self._ELK_INDEX_TYPE_AS = os.environ['ELK_INDEX_TYPE_AS']
            self._elk_ok = True
        except Exception as e:
            print("ENVs failure :(")
            print(e)  
            self._elk_ok = False  

    def _get_dyno_quantity(self):
        """
        This method get the amount of active Dynos from the Heroku account and the application specified.
        """
        # ----------------------------------------
        # GETTING THE AMOUNT OF DYNOS
        url = "https://api.heroku.com/apps/{}/formation".format(self._HEROKU_AS_APP)
        try:
            result = requests.get(url, headers=self._headers)
            for formation in json.loads(result.text):
                self._active_dynos = formation["quantity"]
                return True
            print("No 'quantity' lecture")
            return False
        except Exception as e:
            print("Something bad happend with '_get_dyno_quantity'! (Request)")
            print(e)
            return False


    def _set_number_dynos(self, required_dynos):
        """
        This method exectue the scale action.

            required_dynos: int
                Is the amount of required dynos
        
        @ALERT: This method can impact the billing directly in Heroku account.
        @HARDCODED: The reason to be hardcoded at maximun 5 dynos is to protect the billing impact. 
        """
        self._required_dynos = required_dynos   
        # ----------------------------------------
        # SIZE CONTROL
        if self._required_dynos > self._DYNOSCALER_MAX_DYNOS:
            self._required_dynos = self._DYNOSCALER_MAX_DYNOS
        if self._required_dynos < self._DYNOSCALER_MIN_DYNOS:
            self._required_dynos = self._DYNOSCALER_MIN_DYNOS

        # ----------------------------------------
        # HARDCODED SIZE CONTROL
        if self._required_dynos > 5:
            self._required_dynos = 5
        if self._required_dynos < 1:
            self._required_dynos = 1

        # ----------------------------------------
        # SETTING THE AMOUNT OF DYNOS
        payload = {'quantity': self._required_dynos}
        json_payload = json.dumps(payload)
        url = "https://api.heroku.com/apps/{}/formation/{}".format(self._HEROKU_AS_APP,self._HEROKU_AS_PROCESS)
        try:
            result = requests.patch(url, headers=self._headers, data=json_payload)
            return result.status_code
        except:
            print("Something bad happend with '_set_number_dynos'! (Request)")
            return None
        

    def _scale_up(self):
        """
        This method increases the amount of dynos in one unit if the 'scale_up_cnt' atribute reach HEROKU_UP_CYCLES.
        """
        if self._scale_up_cnt >= self._DYNOSCALER_UP_CYCLES:
            self._scale_up_cnt = 0
            self._scale_down_cnt = 0
            return self._set_number_dynos(self._active_dynos+1)
        else:
            self._scale_up_cnt += 1
            self._scale_down_cnt -= 1
            if self._scale_down_cnt < 0:
                self._scale_down_cnt = 0
            return 0

    def _scale_down(self):
        """
        This method decreases the amount of dynos in one unit if the 'scale_down_cnt' atribute reach HEROKU_DOWN_CYCLES.
        """
        if self._scale_down_cnt >= self._DYNOSCALER_DOWN_CYCLES:
            self._scale_down_cnt = 0
            self._scale_up_cnt = 0
            return self._set_number_dynos(self._active_dynos-1)
        else:
            self._scale_down_cnt += 1
            self._scale_up_cnt -= 1
            if self._scale_up_cnt < 0:
                self._scale_up_cnt = 0
            return 0
    
    def _avg(self, x):
        """
        Util method
        """
        suma = sum(x)
        num = len(x)
        num = num if num else 1
        return suma/num

    def _get_sqs_messages(self):
        """
        This method extract the amount of SQS Messages hold by a specifc AWS SQS Queue.
        The specific metric is 'ApproximateNumberOfMessagesVisible' and is extracted directly from 'CloudWatch'. 
        """ 
        try:
            client = boto3.client('cloudwatch', aws_access_key_id=self._AWS_KEY, aws_secret_access_key=self._AWS_SECRET, region_name=self._AWS_SQS_REGION)
            # GET-METRICS
            response = client.get_metric_data(
                MetricDataQueries=[
                    {
                        'Id': 'visiblesqsmessages',
                        'MetricStat': {
                            'Metric': {
                                'Namespace': 'AWS/SQS',
                                'MetricName': 'ApproximateNumberOfMessagesVisible',
                                'Dimensions': [
                                    {
                                        'Name': 'QueueName',
                                        'Value': self._AWS_SQS_NAME
                                    },
                                ]
                            },
                            'Period': 60,
                            'Stat': 'Average',
                        },
                        'Label': 'VisibleSQSMessages',
                        'ReturnData': True
                    }
                ],
                StartTime=datetime.datetime.utcnow() - datetime.timedelta(minutes=1),
                EndTime=datetime.datetime.utcnow(),
                ScanBy='TimestampDescending',
                MaxDatapoints=100
            )
            metrics = { dic["Label"]:self._avg(dic["Values"]) for dic in response["MetricDataResults"]}
            self._sqs_messages = metrics["VisibleSQSMessages"]
            return True
        except Exception as e:
            print(e)
            return False


    def index_elk(self, url, user, password, index, doc_type, id, body):
        """
        A simple method to send documents to an Elasticseach index intex Clound.
        """
        try:
            es = Elasticsearch(url, http_auth=(user, password),)
            es.index(index=index, doc_type=doc_type, id = id, body=body)
            es.indices.refresh(index=index)
        except Exception as e:
            print(e)

    def run(self):
        """
        This methos perform the logic of scaling up or down, and it is executed once per cycle.
        
        Important:
            Must be executed once per cycle if apscheduler is not used.
        """
        stats_code = None
        err_type = None
        msn = None
        acction = ""
        backLogPerInstance = -1
        desiredInstances = -1
        # -------------------------
        # DynoScaler STATUS CHECK
        if self._status_ok:
            # -------------------------
            # GETTING METRICS
            if self._get_dyno_quantity() and self._get_sqs_messages():
                backLogPerInstance = self._sqs_messages/self._active_dynos
                desiredInstances = self._sqs_messages/self._DYNOSCALER_BACKLOG_THRESHOLD

                # -------------------------
                # SCALING UP / DOWN
                if backLogPerInstance >= self._DYNOSCALER_BACKLOG_THRESHOLD:
                    stats_code = self._scale_up()
                    if stats_code == 200:
                        acction = "scale-up"
                else:
                    stats_code = self._scale_down()
                    if stats_code == 200:
                        acction = "scale-down"

                if stats_code != 200 and stats_code != 0:
                    err_type = "BadStatusCode"
                    msn = "{}".format(stats_code)
            else:
                err_type = "BadMetrics"
                msn = "Dynos: {} | SQSMessages: {}".format(self._active_dynos,self._sqs_messages)
        else:
            err_type = "BadDynoScalerStatus"
            msn = "Envs must be set correctly"
          
        # --------------------------------
        # SENDING INFORMATION TO ELK CLOUD
        data = {}
        data["time"] = datetime.datetime.utcnow()
        data["acction"] = acction
        data["stats_code"] = stats_code
        data["sqs_messages"] = self._sqs_messages
        data["active_dynos"] = self._active_dynos
        data["required_dynos"] = self._required_dynos
        data["backLogPerInstance"] = backLogPerInstance
        data["desiredInstances"] = desiredInstances
        data["scale_up_cnt"] = self._scale_up_cnt
        data["scale_down_cnt"] = self._scale_down_cnt
        data["err_type"] = err_type
        data["msn"] = msn
        print(data)
        if self._elk_ok:
            self.index_elk(self._ELK_ELASTIC_URL, self._ELK_ELASTIC_USER, self._ELK_ELASTIC_PASS, self._ELK_INDEX_AS, self._ELK_INDEX_TYPE_AS, data["time"], data)
        
# --------------------------
# CRON TASK EXECUTION
# CYCLE : 30s
dyno_scaler = DynoScaler()
sched = BlockingScheduler()
@sched.scheduled_job('interval', seconds=30)
def timed_job():
    print("Executing...")
    dyno_scaler.read_ens()
    dyno_scaler.run()
sched.start()