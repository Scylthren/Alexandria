import sqlite3
from pathlib import Path

conn = sqlite3.connect("library.db")

cursor = conn.cursor()

cmd1 = """
SELECT isbn13 FROM books where cover_url IS NULL AND cover_path IS NULL;
"""
cursor.execute(cmd1)
results = cursor.fetchall()

for row in results:
    isbn = row[0]
    cursor.execute("UPDATE books SET cover_path = ? WHERE isbn13 = ?", (f"{isbn}.jpg", isbn))
conn.commit()
conn.close()
