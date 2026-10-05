import sqlite3
import unittest
from backend import note_outcomes as N


class Outcomes(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:'); self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.execute('CREATE TABLE tasks (id,title,status,owner,requester,note,updated,created)')
        self.conn.execute('CREATE TABLE task_relations (from_task,to_task,kind,created_by,created)')
        self.conn.execute("INSERT INTO tasks VALUES ('review','Review meeting','done','bot:coo','human:ana','Created follow-up tasks.','today','today')")
        for id,status in [('a','done'),('b','waiting')]:
            self.conn.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)',(id,'Follow-up',status,'bot:cmo','bot:coo','','today','today'))
            self.conn.execute("INSERT INTO task_relations VALUES (?,'review','parent',NULL,'today')",(id,))
        self.record={'sent':{'task':'review','slug':'coo'}}

    def test_private_task_details_are_not_returned(self):
        result=N.outcome(self.conn,self.record,actor='human:other')
        self.assertNotIn('Created',result['summary'])
        self.assertNotIn('followups',result)

if __name__ == '__main__': unittest.main()
