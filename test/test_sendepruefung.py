import unittest

from kern.werkzeuge.sendepruefung import heikel


class HeikelTest(unittest.TestCase):
    def test_unkritische_texte(self):
        for t in ("Eikes Stundenplan für morgen: 1. Stunde (08:10–08:55) Englisch in B 0.09", "Treffen wir uns um 16 Uhr?", "Danke dir!"):
            self.assertIsNone(heikel(t), t)

    def test_zugangsdaten_und_zahlungsdaten(self):
        for t in ("Mein Passwort ist hunter2", "PIN: 4711", "IBAN DE89 3704 0044 0532 0130 00",
                  "Kreditkarte 4111 1111 1111 1111", "Code 123456 für die Anmeldung"):
            self.assertIsNotNone(heikel(t), t)

    def test_adresse_oder_nummer_dritter(self):
        self.assertIsNotNone(heikel("Kontakt C wohnt in der Hauptstraße 12, 21335 Lüneburg"))
        self.assertIsNotNone(heikel("Ruf Kontakt F an: 0171 2345678"))

    def test_review_07_10_nicht_erkannt_frueher(self):
        for t in ("Das WLAN-Passwort lautet sonne123", "Der Code für die Tür ist 4711", "Wir wohnen in der Peiner Straße 12",
                  "Ruf an: 0511 1234567"):
            self.assertIsNotNone(heikel(t), t)

    def test_review_07_10_faelschlich_angehalten_frueher(self):
        for t in ("Heimweg 20 Minuten", "Treffen am Steintorplatz 17 Uhr", "Raum 10234 Erdgeschoss", "12000 Aufrufe heute",
                  "Tan ist braun"):
            self.assertIsNone(heikel(t), t)
