import logging
import os.path
import time

from swift.common.db import DatabaseConnectionError
from swift.common.ring import Ring
from swift.common.ring.utils import is_local_device
from swift.common.utils import whataremyips, hash_path, storage_directory
from swift.container.backend import DATADIR, ContainerBroker

from .sync_container import SyncContainer


class S3Sync(object):
    def __init__(self, conf):
        self.conf = conf
        self.root = conf['devices']
        self.interval = 10
        self.swift_dir = '/etc/swift'
        self.container_ring = Ring(self.swift_dir, ring_name='container')
        self.retries = 3

        self.scratch = conf['scratch']
        self.myips = whataremyips('0.0.0.0')
        self.items_chunk = conf['items_chunk']
        self.poll_timeout = conf.get('poll_timeout', 5)
        self.logger = logging.getLogger('s3-sync')

    def get_broker(self, account, container, part, node):
        db_hash = hash_path(account, container)
        db_dir = storage_directory(DATADIR, part, db_hash)
        db_path = os.path.join(self.root, node['device'], db_dir,
                               db_hash + '.db')
        return ContainerBroker(db_path, account=account, container=container)

    def sync_row(self, sync_container, row):
        if row['deleted']:
            return sync_container.delete_object(row['name'])
        return sync_container.upload_object(row['name'])

    # TODO: use green threads here: need to understand whether it makes sense
    def sync_items(self, sync_container, rows, nodes_count, node_id):
        for row in rows:
            if (row['ROWID'] % nodes_count) != node_id:
                continue
            if self.logger:
                self.logger.debug('propagating %s' % row['ROWID'])
            self.sync_row(sync_container, row)

        for row in rows:
            # Validate that changes from all other rows have also been sync'd.
            if (row['ROWID'] % nodes_count) == node_id:
                continue
            if self.logger:
                self.logger.debug('verifiying %s' % row['ROWID'])
            self.sync_row(sync_container, row)

    def get_items_since(self, broker, since_start):
        return broker.get_items_since(since_start, 10)

    def container(self, container):
        part, container_nodes = self.container_ring.get_nodes(
            container.account, container.container)
        nodes_count = len(container_nodes)
        for index, node in enumerate(container_nodes):
            if not is_local_device(self.myips, None, node['ip'],
                                   node['port']):
                continue
            sync_meta = container.load_meta()
            if sync_meta:
                start = sync_meta['last_row']
            else:
                start = 0
            broker = self.get_broker(container.account, container.container,
                                     part, node)
            try:
                items = broker.get_items_since(start, self.items_chunk)
            except DatabaseConnectionError:
                continue
            if items:
                self.sync_items(container, items, nodes_count, index)
                sync_meta['last_row'] = items[-1]['ROWID']
                container.store_meta(sync_meta)
            return

    def run_always(self):
        while True:
            start = time.time()
            self.run_once()
            elapsed = time.time() - start
            if elapsed < self.poll_timeout:
                time.sleep(self.poll_timeout - elapsed)

    def run_once(self):
        for sync_settings in self.conf['containers']:
            self.sync_container(SyncContainer(self.scratch, sync_settings))
