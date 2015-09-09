from celery import Celery
from flask import Flask, current_app, request, redirect, session
from flask.ext.kvsession import KVSessionExtension
from .middleware import HTTPMethodOverrideMiddleware
from .views import (create_api_bp, dashboard_bp, create_admin,
                    standalone_pages_bp, store_bp, hooks_bp,
                    app_bp, integrations, market_bp)
from .assets import assets_env
from .vendorapp_views import vendor_bp, create_vendor_api_bp
from .vendorapp_assets import vendorapp_assets_env
from .core import (fedex, listener, security,
                   internal_server_error, redis_store, csrf)
from .models import db, user_datastore, vendor_datastore
from .mailer import mailer
from authenticators import with_signed_authentication, with_basic_authentication
from flask_debugtoolbar import DebugToolbarExtension
import logging
import template_filters
from .responses import exception_response_json
import pygeoip
from flask.ext.login import user_logged_in
from .session_manager import handover_anon_session
from flask.ext.security import Security
from hamlish_jinja import HamlishTagExtension
# from logging.handlers import SMTPHandler
from .app_log_handler import AppLogHandler
from .ext import FlaskClientPlus


class FlaskPlus(Flask):
    jinja_options = Flask.jinja_options
    jinja_options['extensions'].append(HamlishTagExtension)
    test_client_class = FlaskClientPlus


def security_login_processor():
    return dict(register_user_form=forms.ExtendedRegisterForm())


def session_transition_handler(app, user):
    handover_anon_session()
    session.modified = True
    return True


def geo_redirect():
    try:
        gi = pygeoip.GeoIP(current_app.config['GEOIPDAT'])
        request_country = gi.country_name_by_addr(request.remote_addr).lower()
        if request_country != current_app.config['COUNTRY']:
            redirect(current_app.config['SERVER_NAME'])
    except:
        pass


def create_vendor_app(database=db, config_env='INKMONKWEB_VCONFIG',
                      testing=False):
    app = FlaskPlus(__name__)
    app.config.from_object('inkmonkweb.vendorapp_default_config')
    app.config.from_envvar(config_env)
    if testing:
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        app.config['SQLALCHEMY_DATABASE_URI'] = app.config['TESTDB_URI']
        app.config['LOG_FILE_LOC'] = app.config['TESTLOG_LOC']
    app.config['SECURITY_POST_LOGIN_VIEW'] = "/dashboard"
    app.config['SECURITY_POST_REGISTER_VIEW'] = "/dashboard"
    database.init_app(app)
    listener.init_app(app)

    vendorapp_assets_env.init_app(app)
    mailer.init_app(app)
    KVSessionExtension(redis_store, app)
    Security(
        app, vendor_datastore,
        register_form=forms.VendorRegisterForm,
        confirm_register_form=forms.VendorRegisterForm)
    app.register_blueprint(vendor_bp, url_prefix='/dashboard')
    app.register_blueprint(
        create_vendor_api_bp(), url_prefix='/ivapi')
    return app


def create_app(testing=False, database=db, config_env='INKMONKWEB_CONFIG',
               instance_path=None, initialize_blueprints=True):
    if instance_path:
        app = FlaskPlus(__name__, instance_path=instance_path,
                        instance_relative_config=True)
    else:
        app = FlaskPlus(__name__)
    app.config.from_object('inkmonkweb.default_config')
    app.config.from_envvar(config_env)
    if testing:
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        app.config['SQLALCHEMY_DATABASE_URI'] = app.config['TESTDB_URI']
        app.config['LOG_FILE_LOC'] = app.config['TESTLOG_LOC']
    app.errorhandler(500)(internal_server_error)
    # app.before_request(geo_redirect)
    # app.errorhandler(400)(exception_response_json)
    database.init_app(app)
    listener.init_app(app)
    assets_env.init_app(app)
    mailer.init_app(app)
    if not app.config['TESTING']:
        # standalone_pages_bp.before_request(geo_redirect)
        # dashboard_bp.before_request(geo_redirect)
        # app_bp.before_request(geo_redirect)
        csrf.init_app(app)
    KVSessionExtension(redis_store, app)
    # Session(app)
    user_logged_in.connect(session_transition_handler, app)

    # Register Jinja2 Custom filters
    app.jinja_env.filters['timestampize'] = template_filters.timestampize
    app.jinja_env.filters['hash_hmac'] = template_filters.hash_hmac
    app.jinja_env.filters['json'] = template_filters.json_dumps
    app.jinja_env.filters['todict'] = template_filters.todict
    app.jinja_env.add_extension('pyjade.ext.jinja.PyJadeExtension')
    app.jinja_env.hamlish_enable_div_shortcut = True
    app.jinja_env.line_statement_prefix = '%'

    applogger = AppLogHandler(app)
    applogger.start()

    security.init_app(
        app, user_datastore,
        register_form=forms.ExtendedRegisterForm,
        confirm_register_form=forms.ExtendedRegisterForm)

    # security_state.login_context_processor(security_login_processor)

    fedex.init_app(app)
    # app.wsgi_app = HTTPMethodOverrideMiddleware(app.wsgi_app)
    if initialize_blueprints:
        admin = create_admin()
        admin.init_app(app)
        app.register_blueprint(standalone_pages_bp)
        app.register_blueprint(store_bp, url_prefix='/store')
        app.register_blueprint(app_bp, url_prefix='/app')
        app.register_blueprint(market_bp, url_prefix='/market')
        app.register_blueprint(
            create_api_bp(), url_prefix='/json')
        app.register_blueprint(
            create_api_bp(name='api',
                          authenticator=with_basic_authentication,
                          optional_authenticator=with_basic_authentication),
            subdomain='api', url_prefix='/v1')
        # app.register_blueprint(
        #     create_api_bp('testapi', with_basic_authentication),
        #     url_prefix='/test/api/v1')
        app.register_blueprint(dashboard_bp, url_prefix='/dashboard')
        for integration in integrations:
            integration.register(app)
        app.register_blueprint(hooks_bp, url_prefix='/hooks')
        app.blueprints['adminapi'].errorhandler(400)(exception_response_json)
    return app


def create_api_app(database=db, config_env='INKMONKWEB_CONFIG'):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object('inkmonkweb.default_config')
    app.config.from_envvar('INKMONKWEB_CONFIG')
    app.errorhandler(500)(internal_server_error)
    database.init_app(app)
    listener.init_app(app)
    mailer.init_app(app)

    filehandler = logging.FileHandler(filename=app.config['LOG_FILE_LOC'])
    filehandler.setLevel(logging.DEBUG)
    app.logger.addHandler(filehandler)
    if not app.debug:
        mail_handler = SMTPHandler(
            (app.config['MAIL_SERVER'], app.config['MAIL_PORT']),
            'api-server-error@inkmonk.in', app.config['NIGHTS_WATCH'],
            'Inkmonk needs you.',
            credentials=(app.config['MAIL_USERNAME'],
                         app.config['MAIL_PASSWORD']))
        mail_handler.setLevel(logging.ERROR)
        app.logger.addHandler(mail_handler)
    app.wsgi_app = HTTPMethodOverrideMiddleware(app.wsgi_app)
    app.register_blueprint(
        create_api_bp('v1', authenticator=with_basic_authentication,
                      optional_authenticator=with_basic_authentication),
        url_prefix='/v1')
    print app.url_map
    return app


def create_celery(app):
    # app = Flask('swagplus', instance_relative_config=True)
    # app.config.from_object('swagplus.default_config')
    # app.config.from_envvar('STICKYSTAMP_SITE_CONFIG')

    celery = Celery(app.import_name, broker=app.config['CELERY_BROKER_URL'])
    celery.conf.update(app.config)
    # celery.config_from_object('inkmonkweb.celeryconfig')

    TaskBase = celery.Task

    class ContextTask(TaskBase):
        abstract = True

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)

    celery.Task = ContextTask
    return celery
