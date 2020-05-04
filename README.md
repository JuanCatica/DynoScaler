## DynoScaler

### Author:
#### Ing. Mec. Juan Camilo Cática

<b>DynoScaler</b> tracks the amount of SQS messages from an AWS account and the amount of active Dynos from a Heroku application,
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
