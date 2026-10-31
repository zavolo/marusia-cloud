#!/bin/sh
set -e
D="${1:-/tls}"
CA_IN="${2:-/ca-in}"
mkdir -p "$D"
if [ -s "$D/fullchain.pem" ] && [ -s "$D/leaf.key" ]; then
  echo "certs present in $D, skip"
  exit 0
fi
if [ -s "$CA_IN/fullchain.pem" ] && [ -s "$CA_IN/leaf.key" ]; then
  echo "using ready cert from $CA_IN"
  cp "$CA_IN/fullchain.pem" "$D/fullchain.pem"
  cp "$CA_IN/leaf.key" "$D/leaf.key"
  exit 0
fi
apk add --no-cache openssl >/dev/null
SAN="DNS:vc.go.mail.ru,DNS:s1-vc.go.mail.ru,DNS:vc1.go.mail.ru,DNS:voice-test2.ngx.openstack.go.devmail.ru,DNS:vc1.go.devmail.ru"
openssl req -newkey rsa:2048 -nodes \
  -keyout "$D/leaf.key" -out "$D/leaf.csr" -subj "/CN=vc.go.mail.ru"
if [ -s "$CA_IN/ca.crt" ] && [ -s "$CA_IN/ca.key" ]; then
  echo "signing leaf with operator CA from $CA_IN"
  openssl x509 -req -days 3650 \
    -in "$D/leaf.csr" -CA "$CA_IN/ca.crt" -CAkey "$CA_IN/ca.key" -CAcreateserial \
    -out "$D/leaf.crt" -extfile - <<EOF
subjectAltName=$SAN
EOF
  if [ -s "$CA_IN/chain.pem" ]; then
    cat "$D/leaf.crt" "$CA_IN/chain.pem" > "$D/fullchain.pem"
  else
    cat "$D/leaf.crt" "$CA_IN/ca.crt" > "$D/fullchain.pem"
  fi
else
  echo "no operator cert/CA in $CA_IN, generating self-signed CA (trust it on the device)"
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout "$D/ca.key" -out "$D/ca.crt" -subj "/CN=Marusia Local CA"
  openssl x509 -req -days 3650 \
    -in "$D/leaf.csr" -CA "$D/ca.crt" -CAkey "$D/ca.key" -CAcreateserial \
    -out "$D/leaf.crt" -extfile - <<EOF
subjectAltName=$SAN
EOF
  cat "$D/leaf.crt" "$D/ca.crt" > "$D/fullchain.pem"
fi
rm -f "$D/leaf.csr" "$D"/*.srl
echo "ready: $D/fullchain.pem + $D/leaf.key"
