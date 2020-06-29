import json
import os
import traceback

import redis
from rq import Worker, Queue, Connection
from rq.job import Job
from rq.handlers import move_to_failed_queue
from rq.queue import get_failed_queue

listen = ['high', 'default', 'low']

redis_url = os.getenv('REDISTOGO_URL', 'redis://localhost:6379')

redis_conn = redis.from_url(redis_url)

def handle_exception(job, exc_type, exc_value, tb_obj):
    failed_queue = get_failed_queue(redis_conn, job.__class__)

    exc_info_str = json.dumps({
        "message": str(exc_value),
        "traceback": Worker._get_safe_exception_string(traceback.format_exception(exc_type, exc_value, tb_obj))
    })

    failed_queue.quarantine(job, exc_info=exc_info_str)

    return False

def start():
    with Connection(redis_conn):
        worker = Worker(map(Queue, listen), exception_handlers=[handle_exception])
        worker.work()