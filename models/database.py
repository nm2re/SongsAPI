import sqlite3
from Secrets import Token
from passlib.context import CryptContext
## Database Name = Token.DB_NAME


password_context = CryptContext(schemes=["argon2"], deprecated="auto")

def get_connection():
    return sqlite3.connect(Token.DB_NAME)

def initialize_database():
    """
    Initialize database
    """
    conn = get_connection() # connection required to create tables
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS USERS (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        hashed_password TEXT NOT NULL,
        IS_ADMIN BOOLEAN NOT NULL DEFAULT 0
    )
    """)
    conn.commit()
    conn.close()


def create_user(username: str, password: str, is_admin: bool = False):
    """
    Just creates a user
    :return:
    """

    conn = get_connection()
    cursor = conn.cursor()

    hashed_password = password_context.hash(password)
    cursor.execute("""
    INSERT INTO USERS (username, hashed_password, is_admin) VALUES (?,?,?)
    """, (username, hashed_password, int(is_admin)))

    conn.commit()
    conn.close()


def delete_user(username: str):
    """
    Deletes a user
    """

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    DELETE FROM USERS WHERE username = ?
    """, (username,))
    conn.commit()
    conn.close()


def change_password(old_password: str, new_password: str, username: str):


    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT hashed_password FROM USERS WHERE username = ?
    """, (username,))

    result = cursor.fetchone()

    stored_hash = result[0]

    if not password_context.verify(old_password,stored_hash):
        raise ValueError("Current password is incorrect")


    if password_context.verify(new_password, stored_hash):
        raise ValueError("Current password cannot be same as old password")

    new_password_hash = password_context.hash(new_password)
    cursor.execute("""
    UPDATE USERS SET hashed_password = ? WHERE username = ?
    """, (new_password_hash, username))
    conn.commit()
    conn.close()


def check_password(password: str, hashed_password: str) -> bool:
    """
    Checks the password comparing it to the hashed password
    """
    print(f"[CHECK_PASSWORD] Entered password: '{password}'")
    print(f"[CHECK_PASSWORD] Stored hash starts with: {hashed_password[:30]}...")
    result = password_context.verify(password, hashed_password)
    print(f"[CHECK_PASSWORD] Result: {result}")
    return result


def get_user(username: str):
    """
    Fetches a user
    """

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT id, username, hashed_password, is_admin FROM USERS WHERE username = ?
    """, (username,))

    result = cursor.fetchone()
    conn.close()
    if result:
        return {
            "id": result[0],
            "username": result[1],
            "hashed_password": result[2],
            "is_admin": bool(result[3])
        }
    return None


def list_users():
    """
    Lists all the current users
    """

    conn = get_connection()
    cursor = conn.cursor()

    users = []
    cursor.execute("""
    SELECT username, is_admin FROM USERS
    """)

    results = cursor.fetchall()
    conn.close()
    return [{"username": r[0], "is_admin": bool(r[1])} for r in results]


def initial_admin_user():
    """
    Create the first admin user if one doesn't exist
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM USERS WHERE is_admin = 1")
    admin_count = cursor.fetchone()[0]
    conn.close()

    if admin_count == 0:
        # Create default admin user
        create_user("admin", "changeme123", is_admin=True)
        print("[INFO] Default admin user created: admin / changeme123")
    else:
        print("[INFO] Admin user already exists")
