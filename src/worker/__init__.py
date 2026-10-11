"""Background jobs: ingest, training, forecasting and maintenance, run by RQ.

These used to be APScheduler jobs started inside the web process. With gunicorn's
`preload_app = True` that meant the gunicorn master trained models while it supervised the
workers serving requests, so training competed with every request and a crash in it took the
API down. Here every job runs in a separate `rq worker` process, and `rq cron` enqueues them on
schedule (see `worker.cron`).
"""
