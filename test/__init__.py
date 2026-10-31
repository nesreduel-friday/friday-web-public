"""Test-Paket. Hebt das Limit offener Dateien an: Jeder Test mit eigenem Temp-Ordner legt Datenbank-Singletons an
(Protokoll, Bilder, Gedächtnis, Chats …), die bis zum Ende des Laufs offen bleiben — im Betrieb gibt es genau einen
Datenordner, in der Suite über tausend. Bei 1024 offenen Dateien fielen ab etwa Test 2200 zufällige Tests mit
„Too many open files“ um (Teil 2 der Kontakte, 30.09.2026)."""

try:
    import resource

    weich, hart = resource.getrlimit(resource.RLIMIT_NOFILE)
    ziel = 8192 if hart == resource.RLIM_INFINITY else min(8192, hart)
    if weich != resource.RLIM_INFINITY and weich < ziel:
        resource.setrlimit(resource.RLIMIT_NOFILE, (ziel, hart))
except (ImportError, ValueError, OSError):  # kein resource-Modul (Windows) oder nicht erlaubt: Suite läuft wie bisher
    pass
