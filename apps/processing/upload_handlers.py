"""Enforce byte limits while Django parses multipart input, before disk staging."""

from pathlib import Path
import tempfile

from django.core.files.uploadedfile import TemporaryUploadedFile, UploadedFile
from django.core.files.uploadhandler import FileUploadHandler, StopUpload, TemporaryFileUploadHandler

from .storage import media_path


MAX_FILES = 20


class BoundedUploadHandler(FileUploadHandler):
    def __init__(self, request, max_bytes):
        super().__init__(request)
        self.max_bytes = max_bytes
        self.total = self.current = self.files = 0

    def reject(self, message):
        self.request.upload_rejection = message
        raise StopUpload(connection_reset=True)

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.current = 0
        self.files += 1
        if self.files > MAX_FILES:
            self.reject(f"Ajoutez au maximum {MAX_FILES} photos par envoi.")

    def receive_data_chunk(self, raw_data, start):
        self.current += len(raw_data)
        self.total += len(raw_data)
        if self.current > self.max_bytes or self.total > self.max_bytes * MAX_FILES:
            self.reject("Un fichier dépasse la taille maximale autorisée.")
        return raw_data

    def file_complete(self, file_size):
        return None


class PrivateTemporaryUploadHandler(TemporaryFileUploadHandler):
    """Multipart files spill to the private SSD, not the small container tmpfs."""

    def new_file(self, *args, **kwargs):
        FileUploadHandler.new_file(self, *args, **kwargs)
        directory = media_path("temp")
        directory.mkdir(parents=True, exist_ok=True)
        stream = tempfile.NamedTemporaryFile(suffix=".upload", dir=directory)
        self.file = TemporaryUploadedFile.__new__(TemporaryUploadedFile)
        UploadedFile.__init__(
            self.file, file=stream, name=self.file_name, content_type=self.content_type,
            size=0, charset=self.charset, content_type_extra=self.content_type_extra,
        )
