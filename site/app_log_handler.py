from multiprocessing import Queue
from logutils.queue import QueueHandler, QueueListener
from logging.handlers import RotatingFileHandler
from logging.handlers import SMTPHandler
import logging


class AppLogHandler(object):

    def __init__(self, app=None):
        self.logging_queue = Queue()
        self.logging_queue_handler = QueueHandler(self.logging_queue)
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        handlers = []
        filehandler = RotatingFileHandler(filename=app.config['LOG_FILE_LOC'],
                                          maxBytes=1000000, backupCount=5)
        formatter = logging.Formatter(
            "[%(asctime)s] {%(pathname)s:%(lineno)d} %(levelname)s - %(message)s")
        filehandler.setLevel(logging.INFO)
        if app.config['TESTING']:
            filehandler.setLevel(logging.ERROR)
        filehandler.setFormatter(formatter)

        logging.basicConfig()
        handlers.append(filehandler)

        if not app.debug:
            mail_handler = SMTPHandler(
                (app.config['INTERNAL_MAILS_SERVER'],
                 app.config['INTERNAL_MAILS_PORT']),
                app.config['INTERNAL_MAILS_SERVER_USERNAME'],
                app.config['NIGHTS_WATCH'],
                app.config.get('SERVER_ERROR_MAIL_SUBJECT',
                               'Server error'),
                credentials=(app.config['INTERNAL_MAILS_SERVER_USERNAME'],
                             app.config['INTERNAL_MAILS_SERVER_PASSWORD']),
                secure=())
            mail_handler.setLevel(logging.ERROR)
            mail_handler.setFormatter(formatter)
            handlers.append(mail_handler)
        self.logging_queue_listener = QueueListener(
            self.logging_queue, *handlers)
        app.logger.addHandler(self.logging_queue_handler)

    def start(self):
        self.logging_queue_listener.start()

    def stop(self):
        self.logging_queue_listener.stop()
