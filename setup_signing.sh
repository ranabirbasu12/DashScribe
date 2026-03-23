#!/usr/bin/env bash
# Create a persistent self-signed code signing certificate for DashScribe.
#
# This is a ONE-TIME setup. Once the certificate exists in your login keychain,
# every build signed with it keeps the same identity — so macOS permissions
# (Accessibility, Microphone, Screen Recording) survive across rebuilds.
#
# Usage: ./setup_signing.sh
set -euo pipefail

CERT_NAME="DashScribe Developer"
KEYCHAIN="$(security default-keychain | xargs)"

echo "=== DashScribe Code Signing Setup ==="
echo ""

# Check if certificate already exists
if security find-identity -v -p codesigning | grep -q "\"${CERT_NAME}\""; then
    echo "Certificate '${CERT_NAME}' already exists in your keychain."
    echo "No action needed. Your builds will use this certificate automatically."
    echo ""
    security find-identity -v -p codesigning | grep "${CERT_NAME}"
    exit 0
fi

echo "Creating self-signed code signing certificate: '${CERT_NAME}'"
echo "This certificate will be valid for 10 years."
echo ""

# Create temp directory, clean up on exit
TMPDIR_SIGN="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_SIGN"' EXIT

KEY_FILE="$TMPDIR_SIGN/key.pem"
CERT_FILE="$TMPDIR_SIGN/cert.pem"
P12_FILE="$TMPDIR_SIGN/cert.p12"
EXT_FILE="$TMPDIR_SIGN/ext.cnf"
P12_PASS="dashscribe-temp-$(date +%s)"

# OpenSSL extensions config for code signing
cat > "$EXT_FILE" <<'EXTCNF'
[req]
distinguished_name = req_dn
x509_extensions = codesign_ext
prompt = no

[req_dn]
CN = DashScribe Developer

[codesign_ext]
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
basicConstraints = critical, CA:false
EXTCNF

# Generate RSA key + self-signed certificate
openssl genrsa -out "$KEY_FILE" 2048 2>/dev/null
openssl req -new -x509 \
    -key "$KEY_FILE" \
    -out "$CERT_FILE" \
    -days 3650 \
    -config "$EXT_FILE" \
    2>/dev/null

echo "  Generated RSA 2048-bit key + self-signed certificate"

# Package as PKCS12 for keychain import
# -legacy: OpenSSL 3.x uses modern algorithms that macOS Keychain can't read;
#          -legacy forces the older format macOS expects.
openssl pkcs12 -export \
    -legacy \
    -out "$P12_FILE" \
    -inkey "$KEY_FILE" \
    -in "$CERT_FILE" \
    -passout "pass:${P12_PASS}" \
    2>/dev/null

echo "  Packaged as PKCS12"

# Import into login keychain with codesign ACL
security import "$P12_FILE" \
    -k "$KEYCHAIN" \
    -P "$P12_PASS" \
    -T /usr/bin/codesign \
    -T /usr/bin/security

echo "  Imported into keychain: $KEYCHAIN"

# Allow codesign to access without prompting (set partition list)
# This prevents the "always allow" keychain dialog during builds.
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "" "$KEYCHAIN" 2>/dev/null || true

# Trust the certificate for code signing
echo ""
echo "Trusting the certificate for code signing..."
echo "  (You may be prompted for your macOS password — this is expected.)"
echo ""
security add-trusted-cert -d -r trustRoot -p codeSign -k "$KEYCHAIN" "$CERT_FILE"

echo ""
echo "=== Setup complete ==="
echo ""

# Verify
if security find-identity -v -p codesigning | grep -q "\"${CERT_NAME}\""; then
    echo "Certificate '${CERT_NAME}' is ready for use."
    security find-identity -v -p codesigning | grep "${CERT_NAME}"
    echo ""
    echo "Your builds will now use this certificate automatically."
    echo "macOS permissions will persist across rebuilds."
else
    echo "WARNING: Certificate was imported but not found in codesigning identities."
    echo "You may need to open Keychain Access and manually trust it for code signing."
fi
