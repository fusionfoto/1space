#!/bin/bash

set -e

# Make sure all of the .pid files are removed -- services will not start
# otherwise
find /var/lib/ -name *.pid -delete
find /var/run/ -name *.pid -delete

# Include our own internal-client config.
# Ditto for proxy-server config, and a config that will run a no-auth, no-shunt
# proxy-server instance on port 8082.
cp -f /swift-s3-sync/test/container/{internal-client,proxy-server,proxy-server-noshunt}.conf /etc/swift/

# Copied from the docker swift container. Unfortunately, there is no way to
# plugin an additional invocation to start swift-s3-sync, so we had to do this.
/usr/sbin/service rsyslog start
/usr/sbin/service rsync start
/usr/sbin/service memcached start

# set up storage
mkdir -p /swift/nodes/1 /swift/nodes/2 /swift/nodes/3 /swift/nodes/4

for i in `seq 1 4`; do
    if [ ! -e "/srv/$i" ]; then
        ln -s /swift/nodes/$i /srv/$i
    fi
done
mkdir -p /srv/1/node/sdb1 /srv/2/node/sdb2 /srv/3/node/sdb3 /srv/4/node/sdb4 \
    /var/run/swift
/usr/bin/sudo /bin/chown -R swift:swift /swift/nodes /etc/swift /srv/1 /srv/2 \
    /srv/3 /srv/4 /var/run/swift
/usr/bin/sudo -u swift /swift/bin/remakerings

cd /swift-s3-sync; pip install -e .

/usr/bin/sudo -u swift /swift/bin/startmain

python -m s3_sync --log-level debug \
    --config /swift-s3-sync/test/container/swift-s3-sync.conf &
# NOTE: integration tests will run the migrator as needed so they can better
# control the timing of actions.

/usr/bin/java -DLOG_LEVEL=debug -jar /s3proxy/s3proxy \
    --properties /swift-s3-sync/test/container/s3proxy.properties \
    2>&1 > /var/log/s3proxy.log &

sleep 6  # let S3Proxy start up

# Set up stuff for cloud-connector
# NOTE: s3cmd is installed via requirements-test.txt in the Dockerfile
s3cmd -c /swift-s3-sync/s3cfg mb s3://$CONF_BUCKET ||:
s3cmd -c /swift-s3-sync/s3cfg put /swift-s3-sync/test/container/cloud-connector.conf \
    s3://$CONF_BUCKET
s3cmd -c /swift-s3-sync/s3cfg put /swift-s3-sync/test/container/swift-s3-sync.conf \
    s3://$CONF_BUCKET/sync-config.json
s3cmd -c /swift-s3-sync/s3cfg put /swift-s3-sync/test/container/s3-passwd.json \
    s3://$CONF_BUCKET/s3-passwd.json

/usr/local/bin/supervisord -n -c /etc/supervisord.conf
