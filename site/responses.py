from contextlib import contextmanager
import logging
from flask.json import _json
from flask import abort, Response, request, current_app, session
from helpers import dthandler
from functools import wraps
from .utils import deep_group, merge, add_kv_to_dict, dict_map
import models
from .models.modelbase import QueryPlus, is_list_like, is_dict_like
from werkzeug.exceptions import HTTPException
import inspect
from datetime import datetime
from decimal import Decimal
from .models import db
from sqlalchemy.ext.associationproxy import (
    _AssociationDict, _AssociationList)
from .session_manager import current_user_email
import traceback


RESTRICTED = ['limit', 'sort', 'orderby', 'groupby', 'attrs',
              'rels', 'expand', 'offset', 'page', 'per_page']

OPERATORS = ['~', '=', '>', '<', '>=', '!', '<=']
OPERATOR_FUNC = {
    '~': 'ilike', '=': '__eq__', '>': '__gt__', '<': '__lt__',
    '>': '__gt__', '>=': '__ge__', '<=': '__le__', '!': '__ne__'
}


def serialized_obj(obj, attrs_to_serialize=None,
                   rels_to_expand=None,
                   group_listrels_by=None,
                   rels_to_serialize=None,
                   key_modifications=None):
    if obj:
        if hasattr(obj, 'todict'):
            return obj.todict(
                attrs_to_serialize=attrs_to_serialize,
                rels_to_expand=rels_to_expand,
                group_listrels_by=group_listrels_by,
                rels_to_serialize=rels_to_serialize,
                key_modifications=key_modifications)
        return str(obj)
    return None


def serialized_list(olist, **kwargs):
    return map(
        lambda o: serialized_obj(o, **kwargs),
        olist)


def _json_encoder(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, Decimal):
        return str(obj)
    elif isinstance(obj, unicode):
        return obj
    elif isinstance(obj, db.Model):
        return obj.todict()
    elif is_list_like(obj):
        return [_json_encoder(i) for i in obj]
    elif is_dict_like(obj):
        return dict_map(obj, lambda v: _json_encoder(v))
    else:
        try:
            return _json.JSONEncoder().default(obj)
        except Exception as e:
            current_app.logger.debug("Cannot serialize")
            current_app.logger.debug(obj)
            current_app.logger.debug(type(obj))
            current_app.logger.debug(e)
            return unicode(obj)


def jsoned(struct, wrap=True, meta=None):
    if wrap:
        output = {'status': 'success', 'result': struct}
        if meta:
            output = merge(output, meta)
        return _json.dumps(output,
                           default=_json_encoder)
    else:
        return _json.dumps(struct,
                           default=_json_encoder)


def jsoned_obj(obj, **kwargs):
    return jsoned(serialized_obj(obj, **kwargs))


def jsoned_list(olist, **kwargs):
    return jsoned(
        serialized_list(olist, **kwargs))


def success_json():
    return Response(jsoned({'status': 'success'}, wrap=False),
                    200, mimetype='application/json')


def as_json(struct, status=200, wrap=True, meta=None):
    return Response(jsoned(struct, wrap=wrap, meta=meta),
                    status, mimetype='application/json')


def as_json_obj(o, attrs_to_serialize=None,
                rels_to_expand=None,
                rels_to_serialize=None,
                group_listrels_by=None,
                key_modifications=None,
                groupkeys=None,
                meta=None):
    return as_json(serialized_obj(
        o, attrs_to_serialize=attrs_to_serialize,
        rels_to_expand=rels_to_expand,
        rels_to_serialize=rels_to_serialize,
        group_listrels_by=group_listrels_by,
        key_modifications=key_modifications),
        meta=meta)


def as_json_list(olist, attrs_to_serialize=None,
                 rels_to_expand=None,
                 rels_to_serialize=None,
                 group_listrels_by=None,
                 key_modifications=None,
                 groupby=None,
                 keyvals_to_merge=None,
                 meta=None):
    if groupby:
        result_list = deep_group(
            olist, keys=groupby, serializer='todict',
            serializer_kwargs={
                'rels_to_serialize': rels_to_serialize,
                'rels_to_expand': rels_to_expand,
                'attrs_to_serialize': attrs_to_serialize,
                'group_listrels_by': group_listrels_by,
                'key_modifications': key_modifications
            })
    else:
        result_list = serialized_list(
            olist, attrs_to_serialize=attrs_to_serialize,
            rels_to_expand=rels_to_expand,
            group_listrels_by=group_listrels_by,
            rels_to_serialize=rels_to_serialize,
            key_modifications=key_modifications)
        if keyvals_to_merge:
            result_list = [merge(obj_dict, kvdict)
                           for obj_dict, kvdict in
                           zip(result_list, keyvals_to_merge)]
    return as_json(result_list, meta=meta)


def appropriate_json(olist, **kwargs):
    if len(olist) == 1:
        return as_json_obj(olist[0], **kwargs)
    return as_json_list(olist, **kwargs)


@contextmanager
def fallback(code):
    try:
        yield
    except Exception as e:
        logging.exception(e)
        current_app.logger.error(e)
        abort(code)


def error_json(status_code, error=None):
    return Response(_json.dumps({
        'status': 'failure',
        'error': error,
        'url': request.url},
        default=dthandler),
        status_code, mimetype='application/json')


def exception_response_json(exception):
    log = {
        'exception': exception,
        'remote_ip': request.remote_addr
    }
    current_app.logger.exception(exception)
    current_app.logger.error(str(log))
    return Response(_json.dumps({
        'status': 'failure',
        'error': exception.description,
        'url': request.url},
        default=dthandler),
        exception.code, mimetype='application/json')


def not_found_json(error='Not Found'):
    return error_json(404, error)


def not_authorized_json(error='Unauthorized Request'):
    return error_json(401, error)


def method_unexpected_json(error='Request method not expected'):
    return error_json(405, error)


def bad_request_json(exception):
    return error_json(exception.code, error=exception.description)


def _serializable_params(args, check_groupby=False):
    params = {}
    if 'attrs' in args:
        attrs = args.get('attrs')
        if attrs.lower() == 'none':
            params['attrs_to_serialize'] = []
        else:
            params['attrs_to_serialize'] = attrs.split(',')
    if 'rels' in args:
        rels = args.get('rels')
        if rels.lower() == 'none':
            params['rels_to_serialize'] = []
        else:
            params['rels_to_serialize'] = rels.split(',')
    if 'expand' in args:
        expand = args.get('expand')
        if expand.lower() == 'none':
            params['rels_to_expand'] = []
        else:
            params['rels_to_expand'] = expand.split(',')
    if 'grouprelby' in request.args:
        params['group_listrels_by'] = {
            arg.partition(':')[0]: arg.partition(':')[2].split(',')
            for arg in request.args.getlist('grouprelby')}
    if check_groupby and 'groupby' in request.args:
        params['groupby'] = request.args.get('groupby').split(',')
    # if 'grouprelsby' in request.args:
    #     params[]
    # if 'modify' in args:
    #     if attrs.lower() == 'none':
    #         params['key_modifications'] = {}
    #     else:
    #         params['key_modifications'] = dict([
    #             kv.split(':') for kv in args.get('modify').split(',')])
    return params


def as_list(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return as_json_list(
            func(*args, **kwargs),
            **_serializable_params(request.args, check_groupby=True))
    return wrapper


def filter_query_with_key(query, keyword, value, op):
    if '.' in keyword:
        class_name = keyword.partition('.')[0]
        attr_name = keyword.partition('.')[2]
        model_class = getattr(models, class_name)
        _query = query.join(model_class)
    else:
        model_class = query.cls
        attr_name = keyword
        _query = query
    if hasattr(model_class, attr_name):
        key = getattr(model_class, attr_name)
        if op == '~':
            value = "%{0}%".format(value)
        return _query.filter(getattr(
            key, OPERATOR_FUNC[op])(value))
    else:
        return query


def as_processed_list(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        limit = request.args.get('limit', None)
        sort = request.args.get('sort', None)
        orderby = request.args.get('orderby', 'id')
        offset = request.args.get('offset', None)
        page = request.args.get('page', None)
        per_page = request.args.get('per_page', 20)
        func_argspec = inspect.getargspec(func)
        func_args = func_argspec.args
        for kw in request.args:
            if (kw in func_args and kw not in RESTRICTED and
                    not any(request.args.get(kw).startswith(op)
                            for op in OPERATORS)
                    and not any(kw.endswith(op) for op in OPERATORS)):
                kwargs[kw] = request.args.get(kw)
        result = func(*args, **kwargs)
        if not isinstance(result, QueryPlus):
            result = result.query
        for kw in request.args:
            for op in OPERATORS:
                if kw.endswith(op):
                    result = filter_query_with_key(
                        result, kw.rstrip(op), request.args.get(kw), op)
                    break
                elif request.args.get(kw).startswith(op):
                    result = filter_query_with_key(
                        result, kw, request.args.get(kw).lstrip(op), op)
                    break
            else:
                # Well who would've thought that a for else will be appropriate
                # anywhere? Turns out it is here.
                if kw not in RESTRICTED:
                    value = request.args.get(kw)
                    if value.lower() == 'none':
                        value = None
                    result = filter_query_with_key(result, kw, value, '=')
                    # result = result.filter(
                    #     getattr(result.cls, kw) == value)
        if sort:
            if sort == 'asc':
                result = result.asc(orderby)
            elif sort == 'desc':
                result = result.desc(orderby)
        if page:
            pagination = result.paginate(int(page), int(per_page))
            if pagination.total == 0:
                return as_json_list(
                    result,
                    **_serializable_params(request.args, check_groupby=True))
            if int(page) > pagination.pages:
                abort(404)
            return as_json_list(
                pagination.items,
                **add_kv_to_dict(
                    _serializable_params(request.args, check_groupby=True),
                    'meta', {'total_pages': pagination.pages,
                             'total_items': pagination.total
                             }))
        else:
            if limit:
                result = result.limit(limit)
            if offset:
                result = result.offset(int(offset)-1)
            result = result.all()
        return as_json_list(
            result,
            **_serializable_params(request.args, check_groupby=True)
            )
    return wrapper


def as_obj(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return as_json_obj(
            func(*args, **kwargs),
            **_serializable_params(request.args))
    return wrapper


def as_list_or_obj(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return appropriate_json(
            func(*args, **kwargs),
            **_serializable_params(request.args))
    return wrapper


def abort_with(code, description=None):
    def aborter(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except HTTPException as e:
                raise e
            except Exception as e:
                current_app.logger.exception(e)
                info = "IP: %s, Email: %s, Session: %s" % (
                    request.remote_addr, current_user_email(), session['_id'])
                current_app.logger.error(info)
                if description:
                    abort(code, description)
                else:
                    abort(code, str(e))
        return wrapper
    return aborter
