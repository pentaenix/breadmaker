"""Same reviewed envelope/KMS integrity, bounded RPCs without SDK retry loops."""
from hosted.narration.io_limits import io_timeout
from hosted.security.cloud_kms import GoogleKmsKeyWrapper


class _Client:
    def __init__(self, client):
        self.client = client

    def encrypt(self, *, request):
        return self.client.encrypt(request=request, retry=None, timeout=io_timeout())

    def decrypt(self, *, request):
        return self.client.decrypt(request=request, retry=None, timeout=io_timeout())


class GoogleNarrationKeys(GoogleKmsKeyWrapper):
    def __init__(self, resource, *, client=None):
        if client is None:
            from google.cloud import kms
            client = kms.KeyManagementServiceClient()
        super().__init__(resource, client=_Client(client))
