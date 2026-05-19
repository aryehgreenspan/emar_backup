from pydantic import BaseModel


class BackupLogSyncRequest(BaseModel):
    identifier_key: str
