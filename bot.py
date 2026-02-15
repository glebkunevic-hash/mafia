from telebot import TeleBot, types
from time import sleep 
from random import choice, sample
from threading import Timer
from dotenv import load_dotenv
import os
import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAFIA_IMG = os.path.join(BASE_DIR, "mafia.jpg")
CITIZEN_IMG = os.path.join(BASE_DIR, "citizen.jpg")

load_dotenv(os.path.join(BASE_DIR, ".env"))


db.init_db()

load_dotenv()

TOKEN = os.getenv('TOKEN')
bot = TeleBot(TOKEN)

games = {}

def get_game_state(chat_id):
    return games.get(chat_id, {"game": False, "night": False, "timer": None})

def update_game_state(chat_id, key, value):
    if chat_id not in games:
        games[chat_id] = {"game": False, "night": False, "timer": None}
    games[chat_id][key] = value

def get_killed(chat_id, night_flag: bool) -> str:
    if not night_flag:
        u_killed = db.citizen_kill(chat_id)
        return f"Горожане выгнали: {u_killed}"

    u_killed = db.night_resolution(chat_id)
    return f"Этой ночью убиты: {u_killed}"

def autoplay_bots(chat_id, night: bool):
    players_roles = db.get_players_roles(chat_id) or []
    alive_usernames = db.get_all_alive(chat_id) or []
    
    for player_id, username, role in players_roles:

        if player_id >= 5: 
            continue
        if username not in alive_usernames: 
            continue
        
        targets = [u for u in alive_usernames if u != username]
        if not targets: 
            continue
        target = choice(targets)
        
        if not night:
            db.cast_vote("citizen", target, player_id, chat_id)
            print(f"[BOT] {username} (Role: {role}) voting 'citizen' -> {target}")
        else:
            if role == "mafia":
                db.cast_vote("mafia", target, player_id, chat_id)
                print(f"[BOT] {username} (Role: {role}) voting 'mafia' -> {target}")
            elif role == "doctor":
                heal_target = choice(alive_usernames)
                db.cast_vote("doctor", heal_target, player_id, chat_id)
                print(f"[BOT] {username} (Role: {role}) voting 'doctor' -> {heal_target}")
            elif role == "sheriff":
                db.cast_vote("sheriff", target, player_id, chat_id)
                print(f"[BOT] {username} (Role: {role}) voting 'sheriff' -> {target}")
            elif role == "maniac":
                db.cast_vote("maniac", target, player_id, chat_id)
                print(f"[BOT] {username} (Role: {role}) voting 'maniac' -> {target}")


def send_voting_markup(chat_id, vote_type, exclude_name=None):
    alive = db.get_all_alive(chat_id)
    markup = types.InlineKeyboardMarkup()
    for name in alive:
        if name == exclude_name:
            continue
        data = f"vote|{vote_type}|{chat_id}|{name}"
        markup.add(types.InlineKeyboardButton(text=name, callback_data=data))
    return markup

def game_loop_step(chat_id):
    state = get_game_state(chat_id)
    if not state["game"]:
        return

    night = state["night"]
    
    msg = get_killed(chat_id, night)
    bot.send_message(chat_id, msg)

    kicked_afk = db.clear_round(chat_id, reset_dead=False, night=night)
    if kicked_afk:
        bot.send_message(chat_id, f"Выгнаны за АФК: {', '.join(kicked_afk)}")

    winner = db.check_winner(chat_id)
    if winner:
        bot.send_message(chat_id, f"Игра окончена: победили {winner}")
        update_game_state(chat_id, "game", False)
        
        img_path = MAFIA_IMG if winner == "Мафия" or winner == "Маньяк" else CITIZEN_IMG 
        try:
            with open(img_path, 'rb') as photo:
                bot.send_photo(chat_id, photo)
        except Exception:
            pass 

        players = db.get_players_roles(chat_id)
        for pid, name, role in players:
            won = False
            if winner == "Мафия" and role == "mafia": 
                won = True
            if winner == "Горожане" and role not in ["mafia", "maniac"]: 
                won = True
            if winner == "Маньяк" and role == "maniac": 
                won = True
            
            if pid >= 5:
                db.add_stats(name, pid, won)

        db.clear_round(chat_id, reset_dead=True)
        return

    night = not night
    update_game_state(chat_id, "night", night)
    
    alive = db.get_all_alive(chat_id)
    alive_str = "\n".join(alive) if alive else "никого"
    bot.send_message(chat_id, f"В игре:\n{alive_str}")

    settings = db.get_settings(chat_id)
    timer_seconds = settings[0]

    if night:
        bot.send_message(chat_id, "Город засыпает. Наступила ночь!")
        autoplay_bots(chat_id, True)
        
        players = db.get_players_roles(chat_id)
        for pid, name, role in players:
            if pid < 5: 
                continue
            if name not in alive:
                continue
            if role in ["mafia", "doctor", "sheriff", "maniac"]:
                try:
                    action_name = {
                        "mafia": "Кого убить?",
                        "doctor": "Кого лечить?",
                        "sheriff": "Кого проверить?",
                        "maniac": "Кого убить?"
                    }
                    exclude = name if role != "doctor" else None
                    bot.send_message(pid, f"Ваша роль: {role}. {action_name[role]}", 
                                     reply_markup=send_voting_markup(chat_id, role, exclude))
                except Exception:
                    pass
    else:
        bot.send_message(chat_id, f"День! Обсуждение {timer_seconds} сек. Голосуйте!")
        bot.send_message(chat_id, "Голосование!", reply_markup=send_voting_markup(chat_id, "citizen"))
        autoplay_bots(chat_id, False)

    t = Timer(timer_seconds, game_loop_step, args=[chat_id])
    update_game_state(chat_id, "timer", t)
    t.start()


@bot.callback_query_handler(func=lambda call: call.data.startswith('vote'))
def callback_worker(call):
    try:
        _, vote_type, game_chat_id_str, target = call.data.split("|")
        game_chat_id = int(game_chat_id_str)
        user_id = call.from_user.id
        
        success = db.cast_vote(vote_type, target, user_id, game_chat_id)
        if success:
            print(f"[PLAYER] {call.from_user.first_name} voted {vote_type} -> {target}")
        
        if success:
            bot.answer_callback_query(call.id, "Голос принят!")
            if vote_type == "citizen":
                 bot.send_message(game_chat_id, f"{call.from_user.first_name} проголосовал против {target}")
            elif vote_type == "sheriff":
                target_role = "citizen" 
                players = db.get_players_roles(game_chat_id)
                for _, name, role in players:
                    if name == target:
                        is_mafia = (role == "mafia")
                        bot.send_message(user_id, f"Проверка {target}: {'МАФИЯ' if is_mafia else 'Не мафия'}")
                        break
        else:
            bot.answer_callback_query(call.id, "Нельзя голосовать (вы мертвы/нет прав/уже голосовали)", show_alert=True)
            
    except Exception as e:
        print(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "Ошибка")

@bot.message_handler(commands=['start'], chat_types=['private'])
def start_command(message: types.Message):
    bot.send_message(message.chat.id, "Привет! Добавь меня в группу.\n/reg - регистрация\n/game - старт\n/stats - статистика")

@bot.message_handler(commands=['reg'], chat_types=['group', 'supergroup'])
def reg_in_group(message: types.Message):
    chat_id = message.chat.id
    if get_game_state(chat_id)["game"]:
        bot.reply_to(message, "Игра уже идёт! Нельзя зарегистрироваться.")
        return
    db.insert_player(message.from_user.id, message.from_user.first_name, message.chat.id)
    bot.reply_to(message, "Вы в игре!")

@bot.message_handler(commands=['stats'], chat_types=['group', 'supergroup'])
def stats_command(message: types.Message):
    stats = db.get_stats()
    text = "Топ игроков:\n"
    for name, games_cnt, wins in stats:
        text += f"{name}: Игр: {games_cnt}, Побед: {wins}\n"
    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=['config'], chat_types=['group', 'supergroup'])
def config_command(message: types.Message):
    args = message.text.split()
    chat_id = message.chat.id
    try:
        timer = int(args[1]) if len(args) > 1 else None
        mafia = int(args[2]) if len(args) > 2 else None
        db.update_settings(chat_id, timer, mafia)
        bot.send_message(chat_id, f"Настройки обновлены: Таймер={timer or 'Без изм.'}, Мафия={mafia or 'Без изм.'}")
    except ValueError:
        bot.send_message(chat_id, "Использование: /config [секунды] [кол-во мафии]")

@bot.message_handler(commands=['game'], chat_types=['group', 'supergroup'])
def game_start(message: types.Message):
    chat_id = message.chat.id
    state = get_game_state(chat_id)
    
    if state["game"]:
        bot.send_message(chat_id, "Игра уже идет!")
        return

    try:
        member = bot.get_chat_member(chat_id, message.from_user.id)
        if member.status not in ["administrator", "creator"]:
            bot.send_message(chat_id, "Только админ может запустить игру!")
            return
    except Exception:
        pass

    update_game_state(chat_id, "game", True)
    update_game_state(chat_id, "night", False)

    db.clear_round(chat_id, reset_dead=True)

    players_count = db.players_amount(chat_id)
    if players_count < 5:
        bot.send_message(chat_id, "Добавляю ботов...")
        bot_names = ["Вася", "Петя", "Коля", "Света", "Оля", "Катя", "Дима", "Саша", "Лена", "Маша"]
        chosen = sample(bot_names, 5)
        for i, name in enumerate(chosen):
            db.insert_player(i, name, chat_id)
            sleep(0.1)
    
    db.set_roles(chat_id)
    
    players_roles = db.get_players_roles(chat_id)
    mafia_usernames = db.get_mafia_usernames(chat_id)
    
    for player_id, _, role in players_roles:
        if player_id >= 5:
            try:
                bot.send_message(player_id, f"Ваша роль: {role}")
                if role == 'mafia':
                    bot.send_message(player_id, f"Мафия: {mafia_usernames}")
                print(f"[ROLE] User {player_id} is {role}")
            except:
                bot.send_message(chat_id, f"Откройте ЛС с ботом для получения роли! (@{bot.get_me().username})")

    bot.send_message(chat_id, "Игра началась! 10 сек на знакомство...")
    
    t = Timer(10, game_loop_step, args=[chat_id])
    update_game_state(chat_id, "timer", t)
    t.start()


bot.polling(non_stop=True)