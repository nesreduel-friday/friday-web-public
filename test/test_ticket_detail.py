import unittest

from kern.garantie.abschicken import ticket_detail


class TicketDetailTest(unittest.TestCase):
    def test_reines_deutschlandticket(self):
        for name in ("Deutschlandticket", "D-Ticket", " deutschlandticket "):
            self.assertEqual(ticket_detail({"art": "Deutschlandticket", "bezeichnung": name}), "Deutschlandticket")

    def test_zusatz_bleibt_vorrangig(self):
        self.assertEqual(ticket_detail({"art": "Deutschlandticket", "bezeichnung": "D-Ticket Hannover Jugend"}),
                         "Deutschlandticket (Hannover Jugend)")

    def test_unklar_bleibt_unklar(self):
        self.assertIsNone(ticket_detail({"art": "Deutschlandticket", "bezeichnung": "Deutschlandticket Plus"}))


if __name__ == "__main__":
    unittest.main()
