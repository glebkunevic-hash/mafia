import sqlite3 
import random
from pathlib import Path
from traceback import print_exc
import os


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "db.db"


def connect(func):
    def wrapper(*args, **kwargs):
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        result = None
        try:
            result = func(cur, *args, **kwargs)
            conn.commit()
        except Exception:
            conn.rollback()
            print(f"[ERROR]: {func.__name__}:")
            print_exc()
        finally:
            conn.close()
        return result
    return wrapper

 
@connect
def init_db(cur):
    # Удаляем старую таблицу если она существует
    cur.execute("DROP TABLE IF EXISTS players")
    cur.execute("DROP TABLE IF EXISTS votes")
    cur.execute("DROP TABLE IF EXISTS stats")
    cur.execute("DROP TABLE IF EXISTS settings")
    
    # Создаем таблицы заново с правильной структурой
    cur.execute("""
        CREATE TABLE players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER,
            username TEXT,
            chat_id INTEGER,
            role TEXT DEFAULT 'citizen',
            dead INTEGER DEFAULT 0,
            voted INTEGER DEFAULT 0,
            afk_count INTEGER DEFAULT 0,
            UNIQUE(player_id, chat_id)
        )""")
    
    cur.execute("""
        CREATE TABLE votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vote_type TEXT NOT NULL,
            target_name TEXT NOT NULL,
            voted_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            FOREIGN KEY (voted_id) REFERENCES players(id))
    """)

    cur.execute("""
        CREATE TABLE stats (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            games INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE settings (
            chat_id INTEGER PRIMARY KEY,
            timer_seconds INTEGER DEFAULT 30,
            mafia_count INTEGER DEFAULT 1
        )
    """)


@connect
def insert_player(cur, player_id: int, username: str, chat_id: int) -> None:
    cur.execute("""
    INSERT OR REPLACE INTO players(player_id, username, chat_id, dead, voted, afk_count)
    VALUES (?, ?, ?, COALESCE((SELECT dead FROM players WHERE player_id=? AND chat_id=?), 0), 0, 0)
    """, (player_id, username, chat_id, player_id, chat_id))


@connect
def players_amount(cur, chat_id: int) -> int:
    cur.execute("SELECT COUNT(*) FROM players WHERE chat_id=?", (chat_id,))
    result = cur.fetchone()
    return result[0] if result else 0


@connect
def get_mafia_usernames(cur, chat_id: int) -> str:
    cur.execute("SELECT username FROM players WHERE role = 'mafia' AND dead = 0 AND chat_id=?", (chat_id,))
    rows = cur.fetchall()
    return "\n".join(row[0] for row in rows)


@connect
def get_players_roles(cur, chat_id: int) -> list:
    cur.execute("SELECT player_id, username, role FROM players WHERE chat_id=?", (chat_id,))
    return cur.fetchall()


@connect
def get_all_alive(cur, chat_id: int) -> list:
    cur.execute("SELECT username FROM players WHERE dead=0 AND chat_id=?", (chat_id,))
    return [row[0] for row in cur.fetchall()] 


@connect
def set_roles(cur, chat_id: int) -> None:
    cur.execute("SELECT player_id FROM players WHERE chat_id=? ORDER BY player_id", (chat_id,))
    players_rows = cur.fetchall()
    n = len(players_rows)
    if n == 0:
        return 
    
    cur.execute("SELECT mafia_count FROM settings WHERE chat_id=?", (chat_id,))
    res = cur.fetchone()
    mafia_count = res[0] if res else max(1, int(n * 0.3))

    roles = ["mafia"] * mafia_count
    
    special_roles = []
    if n >= 5: 
        special_roles.append("doctor")
    if n >= 6: 
        special_roles.append("sheriff")
    if n >= 7: 
        special_roles.append("maniac")

    assigned_count = len(roles) + len(special_roles)
    if assigned_count > n:
        special_roles = special_roles[:n-len(roles)]
    
    roles.extend(special_roles)
    roles.extend(["citizen"] * (n - len(roles)))
    
    random.shuffle(roles)
    
    for (player_id,), role in zip(players_rows, roles):
        cur.execute("UPDATE players SET role=?, dead=0, voted=0, afk_count=0 WHERE player_id=? AND chat_id=?", (role, player_id, chat_id))


@connect
def user_exists(cur, player_id: int, chat_id: int) -> bool:
    cur.execute("SELECT 1 FROM players WHERE player_id=? AND chat_id=?", (player_id, chat_id))
    return cur.fetchone() is not None


@connect
def cast_vote(cur, vote_type: str, target_name: str, voted_id: int, chat_id: int) -> bool:
    cur.execute("SELECT dead, voted, role FROM players WHERE player_id = ? AND chat_id=?", (voted_id, chat_id))
    row = cur.fetchone()
    if not row: 
        return False
    dead, voted, role = row 
    
    if dead != 0 or voted != 0: 
        return False 

    if vote_type == "mafia" and role != "mafia": 
        return False
    if vote_type == "doctor" and role != "doctor": 
        return False
    if vote_type == "sheriff" and role != "sheriff": 
        return False
    if vote_type == "maniac" and role != "maniac": 
        return False

    cur.execute("SELECT 1 FROM players WHERE username = ? AND dead = 0 AND chat_id=?", (target_name, chat_id))
    if not cur.fetchone(): 
        return False
    
    cur.execute("INSERT INTO votes (vote_type, target_name, voted_id, chat_id) VALUES (?, ?, ?, ?)", 
                (vote_type, target_name, voted_id, chat_id))
    cur.execute("UPDATE players SET voted = 1 WHERE player_id = ? AND chat_id=?", (voted_id, chat_id))
    return True 


@connect
def night_resolution(cur, chat_id: int) -> str:
    cur.execute("""SELECT target_name FROM votes WHERE vote_type='mafia' AND chat_id=? 
                   GROUP BY target_name ORDER BY COUNT(*) DESC LIMIT 1""", (chat_id,))
    mafia_target = cur.fetchone()
    mafia_target = mafia_target[0] if mafia_target else None

    cur.execute("""SELECT target_name FROM votes WHERE vote_type='maniac' AND chat_id=? 
                   GROUP BY target_name ORDER BY COUNT(*) DESC LIMIT 1""", (chat_id,))
    maniac_target = cur.fetchone()
    maniac_target = maniac_target[0] if maniac_target else None

    cur.execute("""SELECT target_name FROM votes WHERE vote_type='doctor' AND chat_id=? 
                   GROUP BY target_name ORDER BY COUNT(*) DESC LIMIT 1""", (chat_id,))
    doctor_target = cur.fetchone()
    doctor_target = doctor_target[0] if doctor_target else None

    dead_list = []
    
    if mafia_target:
        if mafia_target != doctor_target:
            dead_list.append(mafia_target)
    
    if maniac_target:
        if maniac_target != doctor_target:
            dead_list.append(maniac_target)
    
    dead_list = list(set(dead_list))
    for username in dead_list:
        cur.execute("UPDATE players SET dead = 1 WHERE username = ? AND chat_id=?", (username, chat_id))
    
    return ", ".join(dead_list) if dead_list else "Никого"


@connect
def citizen_kill(cur, chat_id: int) -> str:
    cur.execute("""
        SELECT target_name, COUNT(*) AS count
        FROM votes 
        WHERE vote_type='citizen' AND chat_id = ?
        GROUP BY target_name 
        ORDER BY count DESC
        LIMIT 2
    """, (chat_id,))
    rows = cur.fetchall()
    if not rows: 
        return "Никого"
    
    top = rows[0]
    if len(rows) > 1 and rows[1][1] == top[1]: 
        return "Никого"

    cur.execute("UPDATE players SET dead = 1 WHERE username = ? AND chat_id=?", (top[0], chat_id))
    return top[0]


@connect
def check_winner(cur, chat_id: int) -> str | None:
    cur.execute("SELECT COUNT(*) FROM players WHERE role='mafia' AND dead=0 AND chat_id=?", (chat_id,))
    mafia_alive = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM players WHERE role='maniac' AND dead=0 AND chat_id=?", (chat_id,))
    maniac_alive = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM players WHERE role!='mafia' AND role!='maniac' AND dead=0 AND chat_id=?", (chat_id,))
    citizen_alive = cur.fetchone()[0]

    if maniac_alive > 0 and mafia_alive == 0 and citizen_alive <= 1:
        return "Маньяк"

    if mafia_alive >= (citizen_alive + maniac_alive) and mafia_alive > 0:
        return "Мафия"
    
    if mafia_alive == 0 and maniac_alive == 0:
        return "Горожане"
    
    return None


@connect
def clear_round(cur, chat_id: int, reset_dead: bool = False, night: bool = None) -> list:
    kicked_list = []
    if not reset_dead:
        query = "SELECT username, afk_count FROM players WHERE voted=0 AND dead=0 AND chat_id=?"
        params = [chat_id]
        if night:
            query = "SELECT username, afk_count FROM players WHERE voted=0 AND dead=0 AND chat_id=? AND role != 'citizen'"

        cur.execute(query, params)
        afk_players = cur.fetchall()
        for username, count in afk_players:
            new_count = count + 1
            if new_count >= 2:
                cur.execute("UPDATE players SET dead=1 WHERE username=? AND chat_id=?", (username, chat_id))
                kicked_list.append(username)
            else:
                cur.execute("UPDATE players SET afk_count=? WHERE username=? AND chat_id=?", (new_count, username, chat_id))
        
    cur.execute("UPDATE players SET voted=0 WHERE chat_id=?", (chat_id,))
    cur.execute("DELETE FROM votes WHERE chat_id=?", (chat_id,))

    if reset_dead:
        cur.execute("UPDATE players SET dead=0, voted=0, afk_count=0 WHERE chat_id=?", (chat_id,))
    
    return kicked_list


@connect
def add_stats(cur, username: str, user_id: int, win: bool):
    cur.execute("INSERT OR IGNORE INTO stats(user_id, username) VALUES(?, ?)", (user_id, username))
    cur.execute("UPDATE stats SET games = games + 1 WHERE user_id=?", (user_id,))
    if win:
        cur.execute("UPDATE stats SET wins = wins + 1 WHERE user_id=?", (user_id,))


@connect
def get_stats(cur) -> list:
    cur.execute("SELECT username, games, wins FROM stats ORDER BY wins DESC LIMIT 10")
    return cur.fetchall()


@connect
def get_settings(cur, chat_id: int) -> tuple:
    cur.execute("SELECT timer_seconds, mafia_count FROM settings WHERE chat_id=?", (chat_id,))
    res = cur.fetchone()
    if not res:
        cur.execute("INSERT INTO settings(chat_id) VALUES(?)", (chat_id,))
        return (30, 1)
    return res


@connect
def update_settings(cur, chat_id: int, timer: int = None, mafia: int = None):
    if timer is not None:
        cur.execute("""
            INSERT OR REPLACE INTO settings(chat_id, timer_seconds, mafia_count) 
            VALUES (?, ?, COALESCE((SELECT mafia_count FROM settings WHERE chat_id=?), 1))
        """, (chat_id, timer, chat_id))
    if mafia is not None:
        cur.execute("""
            UPDATE settings SET mafia_count=? WHERE chat_id=?
        """, (mafia, chat_id))


if __name__ == "__main__":
    # Удаляем старый файл базы данных
    if DB_PATH.exists():
        os.remove(DB_PATH)
    init_db()