from flask.testing import FlaskClient
from flask.json import _json as json
from .json_plus import _json_encoder
from toolspy import merge


class FlaskClientPlus(FlaskClient):

    def jpost(self, url, **kwargs):
        kwargs['content_type'] = "application/json"
        kwargs['data'] = json.dumps(
            kwargs['data'], default=_json_encoder)
        return self.post(url, **kwargs)

    def jput(self, url, **kwargs):
        kwargs['content_type'] = "application/json"
        kwargs['data'] = json.dumps(
            kwargs['data'], default=_json_encoder)
        return self.put(url, **kwargs)

    def jpatch(self, url, **kwargs):
        kwargs['content_type'] = "application/json"
        kwargs['data'] = json.dumps(
            kwargs['data'], default=_json_encoder)
        return self.patch(url, **kwargs)

    def jread(self, resp):
        return json.loads(resp.data)

    def jget(self, *args, **kwargs):
        return self.jread(self.get(*args, **kwargs))

    def upload(self, url, file_key, file_path, **kwargs):
        buffered = kwargs.pop('buffered', True)
        content_type = kwargs.pop('content_type', 'multipart/form-data')
        kwargs['data'] = merge(
            kwargs['data'],
            {file_key: (open(file_path, 'rb'), file_path)})
        return self.post(url, buffered=buffered, content_type=content_type,
                         **kwargs)
