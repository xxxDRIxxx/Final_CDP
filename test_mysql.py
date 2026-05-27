import mysql.connector

conn = mysql.connector.connect(
    host="127.0.0.1",
    user="root",
    password="chatbot_cavite@1234",
    database="uniwise_db"
)

cursor = conn.cursor()
cursor.execute("SELECT 1")
print(cursor.fetchone())

cursor.close()
conn.close()
