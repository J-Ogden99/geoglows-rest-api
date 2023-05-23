import boto3
import logging
import time
import sys
from constants import LOG_GROUP_NAME, LOG_STREAM_NAME

# Create a CloudWatch Logs client
client = boto3.client('logs')
# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def log_request(**kwargs):
    # Construct the log message
    if 'version' not in kwargs or 'product_no' not in kwargs or 'format' not in kwargs:
        return
    message = f'{kwargs["version"]}_{kwargs["product_no"]}_{kwargs["format"]}'
    if 'region_no' in kwargs:
        if kwargs["region_no"] is not None:
            message += f'_{kwargs["region_no"]}'
    if 'link_no' in kwargs:
        if kwargs["link_no"] is not None:
            message += f'_{kwargs["link_no"]}'
    if 'source' in kwargs and kwargs["source"] is not None:
        message += f'_{kwargs["source"]}'
    else:
        message += '_2'

    # Send the log message to CloudWatch
    response = client.put_log_events(
        logGroupName=LOG_GROUP_NAME,
        logStreamName=LOG_STREAM_NAME,
        logEvents=[
            {
                'timestamp': int(round(time.time() * 1000)),
                'message': message
            }
        ]
    )

    # Print the response from CloudWatch
    # print(response)
