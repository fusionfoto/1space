from swift.common import swob, utils
import swiftclient
import urllib


class DestinationDelete(object):
    def __init__(self, app, conf):
        self.app = app
        self.conf = conf
        self.logger = utils.get_logger(
            conf, name='proxy-server:destination_delete',
            log_route='destination-delete')

    def __call__(self, env, start_response):
        req = swob.Request(env)

        try:
            vers, acct, cont, obj = req.split_path(3, 4, True)
        except ValueError:
            return self.app(env, start_response)

        if req.method != 'DELETE':
            return self.app(env, start_response)

        if not cont or not obj:
            return self.app(env, start_response)

        # HEAD the object, so we can use the If-Unmodified-Since on DELETE
        head_req = swob.Request(dict(env))
        head_req.method = 'HEAD'
        head_status, head_resp, _ = head_req.call_application(self.app)

        status, headers, app_iter = req.call_application(self.app)
        if not status.startswith('404') and not status.startswith('204'):
            start_response(status, headers)
            return app_iter

        if not head_status.startswith('200'):
            start_response(status, headers)
            return app_iter

        scheme, rest = self.conf.get('authurl').split(':', 1)
        host = urllib.splithost(rest)[0]
        path = '/%s/%s' % (vers, acct)
        client = swiftclient.client.Connection(
            authurl=self.conf.get('authurl'),
            user=self.conf.get('remote_user'),
            key=self.conf.get('remote_key'),
            os_options={
                'object_storage_url': '%s:%s%s' % (scheme, host, path)})
        try:
            headers_dict = dict(head_resp)
            client.head_object(
                cont, obj,
                headers={'If-Unmodified-Since': headers_dict['Last-Modified'],
                         'If-Match': headers_dict['Etag']}
            )
            client.delete_object(cont, obj)
        except swiftclient.exceptions.ClientException as e:
            if e.http_status != 304 and e.http_status != 412:
                self.logger.error(
                    'Failed to remove the remote object %s/%s: %s',
                    (cont, obj, str(e)))

        start_response(status, headers)
        return app_iter


def filter_factory(global_conf, **local_conf):
    conf = dict(global_conf, **local_conf)

    def app_filter(app):
        return DestinationDelete(app, conf)
    return app_filter
