#!/bin/sh
set -e
apk add --no-cache dnsmasq >/dev/null
IP="${MARUSIA_DNS_TARGET:-127.0.0.1}"
UPSTREAM="${MARUSIA_DNS_UPSTREAM:-1.1.1.1}"
CONF=/etc/dnsmasq.marusia.conf
{
  echo "no-resolv"
  echo "no-hosts"
  echo "server=$UPSTREAM"
  for H in vc.go.mail.ru s1-vc.go.mail.ru vc1.go.mail.ru voice-test2.ngx.openstack.go.devmail.ru vc1.go.devmail.ru; do
    echo "address=/$H/$IP"
  done
} > "$CONF"
echo "dnsmasq: 5 marusia hosts -> $IP, upstream $UPSTREAM"
exec dnsmasq -k -C "$CONF"
