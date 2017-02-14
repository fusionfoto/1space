import hashlib
import urllib

from swift.common.utils import FileLikeIter


SWIFT_USER_META_PREFIX = 'x-object-meta-'
MANIFEST_HEADER = 'x-object-manifest'


class FileWrapper(object):
    def __init__(self, swift_client, account, container, key, headers={}):
        self._swift = swift_client
        self._account = account
        self._container = container
        self._key = key
        self.swift_req_hdrs = headers
        self._bytes_read = 0
        self.open_object_stream()

    def open_object_stream(self):
        status, self._headers, body = self._swift.get_object(
            self._account, self._container, self._key,
            headers=self.swift_req_hdrs)
        if status != 200:
            raise RuntimeError('Failed to get the object')
        self._swift_stream = body
        self._iter = FileLikeIter(body)
        self._s3_headers = convert_to_s3_headers(self._headers)

    def seek(self, pos, flag=0):
        if pos != 0:
            raise RuntimeError('Arbitrary seeks are not supported')
        if self._bytes_read == 0:
            return
        self._swift_stream.close()
        self.open_object_stream()

    def read(self, size=-1):
        data = self._iter.read(size)
        self._bytes_read += len(data)
        # TODO: we do not need to read an extra byte after
        # https://review.openstack.org/#/c/363199/ is released
        if self._bytes_read == self.__len__():
            self._iter.read(1)
            self._swift_stream.close()
        return data

    def __len__(self):
        if 'Content-Length' not in self._headers:
            raise RuntimeError('Length is not implemented')
        return int(self._headers['Content-Length'])

    def __iter__(self):
        return self._iter

    def get_s3_headers(self):
        return self._s3_headers


def convert_to_s3_headers(swift_headers):
    s3_headers = {}
    for hdr in swift_headers.keys():
        if hdr.lower().startswith(SWIFT_USER_META_PREFIX):
            s3_header_name = hdr[len(SWIFT_USER_META_PREFIX):].lower()
            s3_headers[s3_header_name] = urllib.quote(swift_headers[hdr])
        elif hdr.lower() == MANIFEST_HEADER:
            s3_headers[MANIFEST_HEADER] = urllib.quote(swift_headers[hdr])

    return s3_headers


def is_object_meta_synced(s3_meta, swift_meta):
    swift_keys = set([key.lower()[len(SWIFT_USER_META_PREFIX):]
                      for key in swift_meta
                      if key.lower().startswith(SWIFT_USER_META_PREFIX)])
    s3_keys = set([key.lower() for key in s3_meta.keys()])
    if set(swift_keys) != set(s3_keys):
        return False
    for key in s3_keys:
        swift_value = urllib.quote(swift_meta[SWIFT_USER_META_PREFIX + key])
        if s3_meta[key] != swift_value:
            return False
    return True


def get_slo_etag(manifest):
    etags = [segment['hash'].decode('hex') for segment in manifest]
    md5_hash = hashlib.md5()
    md5_hash.update(''.join(etags))
    return md5_hash.hexdigest() + '-%d' % len(manifest)
