import json
import os
import traceback

import redis
from rq import Worker, Queue, Connection
from rq.job import Job

listen = ['high', 'default', 'low']

redis_url = os.getenv('REDISTOGO_URL', 'redis://localhost:6379')

redis_conn = redis.from_url(redis_url)

def handle_exception(job, exc_type, exc_value, tb_obj):
    exc_info_str = json.dumps({
        "message": str(exc_value),
        "traceback": Worker._get_safe_exception_string(traceback.format_exception(exc_type, exc_value, tb_obj))
    })

    job.failed_job_registry.add(job, exc_string=exc_info_str)

    return False

def start():
    with Connection(redis_conn):
        worker = Worker(map(Queue, listen), exception_handlers=[handle_exception])
        worker.work()