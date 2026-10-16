# Google Drive backup destination

Google Drive is an optional online backup destination for the **local** editor. Local writing and local backups work without it.

## Connect your own Google Drive

1. Create a Google Cloud OAuth client of type **Desktop app** for your own use and enable the Google Drive API. This is the setup for your backup destination, not the Breadmaker hosted service.
2. In Breadmaker, open **Backup locations → Add Destination → Google Drive**.
3. Enter the Desktop OAuth **Client ID** and **Client Secret**, then choose **Connect** and complete Google's authorization flow.
4. Wait for Breadmaker to upload and verify a package. Check the destination's reported freshness before trusting it as a current backup.

Breadmaker uses the limited `drive.file` OAuth scope; it does not ask for your Google password, an API key, or a service account. The Desktop OAuth configuration is user-owned; Breadmaker does not ship a universal shared client secret.

The Client ID is stored for this installation under `google_oauth_client.json` in the Breadmaker data directory (`~/.breadmaker-editor/` by default, or `BREADMAKER_HOME`). The Client Secret is masked in the interface and isn't returned to the browser after submission. The installation-local credential store obscures stored tokens and secrets against casual inspection; **this is not strong protection against someone who already controls the local user account**.

Credentials, refresh tokens, and the local obscuring key are deliberately excluded from project exports, `.breadmaker` packages, Download My Data, and release archives.

## How Breadmaker verifies a remote copy

An upload alone does not mean a backup is safe to restore. Breadmaker downloads the completed Drive object again, verifies the `.breadmaker` package, and only then marks the destination up to date. A fresh local installation can discover surviving Drive backups through its recovery workflow, subject to fresh authorization.

To limit storage growth, the remote destination retains a bounded history of snapshots rather than a full second revision database. Current retention is at most 25 packages per project: up to four from the newest day, one per day for the previous 13 days, and up to eight older weekly copies.

**Google Drive requires internet access.** It is optional and does not turn local editing into a Cloud-dependent service.
