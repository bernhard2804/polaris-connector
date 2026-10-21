# Signierte Erweiterung

`polaris-chatgpt-connector-<Version>-signed.xpi` ist die von Mozilla (AMO,
unlisted, Add-on 3070992) signierte Fassung, wie sie in Firefox installiert
ist. Sie liegt hier, damit sie nach einer Neuinstallation sofort verfügbar
ist – ohne Developer-Hub-Login.

Installieren: `firefox dist/polaris-chatgpt-connector-0.4.2-signed.xpi`
(oder Datei in ein Firefox-Fenster ziehen), dann in `about:addons` →
Polaris → Berechtigungen „Zugriff auf chatgpt.com / claude.ai“ erlauben.

Nach jeder neuen Signierung die neue Datei hier ablegen und die alte
entfernen (`install.sh` nimmt die neueste).
