"""
PyInstaller runtime hook: ensure certifi CA bundle is discoverable
inside the packaged exe. Sets SSL_CERT_FILE and REQUESTS_CA_BUNDLE
to certifi's cacert.pem so requests/urllib3/httpx find root certs.
"""
import os
import sys
import certifi

_ca_bundle = certifi.where()

# Ensure the path exists inside the _MEI* temp dir
if os.path.isfile(_ca_bundle):
    os.environ.setdefault("SSL_CERT_FILE", _ca_bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _ca_bundle)
    # urllib3 respects this env var directly
    os.environ.setdefault("CURL_CA_BUNDLE", _ca_bundle)
