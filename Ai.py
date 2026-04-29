import streamlit as st
import time
import datetime
import re
import threading
import json
from openai import OpenAI
import os
import sqlite3
import pandas as pd
from dotenv import load_dotenv

# ========== 加载环境变量 ==========
load_dotenv(r"G:\PythonAI\AGENT\.venv\AI\KEY.env")
key = os.getenv("ZHIPU_API_KEY")

# ========== SQLite 数据库配置 ==========
DB_PATH = "canteen.db"          # 数据库文件，会在项目根目录自动创建

# ---------- 初始化数据库：创建表（如果不存在）并插入示例菜品 ----------
def init_db():
    """创建所有必需的表，并插入示例菜品数据（如果 dishes 表为空）。"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. 菜品表 dishes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dishes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            calories INTEGER,
            protein REAL,
            fat REAL,
            taste TEXT,
            health_goal TEXT,
            allergens TEXT,
            price REAL,
            category TEXT,
            floor TEXT
        )
    ''')

    # 2. 用户画像表 user_profiles
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id TEXT PRIMARY KEY,
            height_cm INTEGER,
            weight_kg REAL,
            dietary_habit TEXT,
            hobby TEXT,
            exercise_frequency TEXT,
            extra_info TEXT,          -- 存储 JSON 字符串
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 3. 对话历史表 user_conversation_history
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_conversation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            timestamp TEXT,
            messages TEXT NOT NULL,   -- 存储 JSON 字符串
            preview TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 检查是否已有菜品数据，若无则插入示例数据（覆盖各楼层、口味、健康目标）
    cursor.execute("SELECT COUNT(*) FROM dishes")
    if cursor.fetchone()[0] == 0:
        sample_dishes = [
            # 1F 菜品
            ("清炒西兰花", 120, 5, 3, "清淡", "减脂", "无", 8, "素菜", "1F"),
            ("香辣鸡丁", 350, 25, 18, "辣", "增肌", "花生", 15, "肉类", "1F"),
            ("红烧鱼块", 280, 22, 12, "咸鲜", "常规", "鱼类", 18, "鱼类", "1F"),
            # 2F 菜品
            ("蒜蓉空心菜", 90, 4, 2, "清淡", "减脂", "无", 6, "素菜", "2F"),
            ("黑椒牛肉", 420, 30, 22, "辣", "增肌", "牛肉", 22, "肉类", "2F"),
            ("清蒸鲈鱼", 210, 28, 8, "清淡", "常规", "鱼类", 25, "鱼类", "2F"),
            # 3F 菜品
            ("麻婆豆腐", 200, 12, 14, "辣", "常规", "大豆", 10, "素菜", "3F"),
            ("糖醋里脊", 380, 18, 20, "酸甜", "常规", "猪肉", 20, "肉类", "3F"),
            ("香煎带鱼", 310, 24, 18, "咸鲜", "常规", "鱼类", 22, "鱼类", "3F"),
        ]
        cursor.executemany('''
            INSERT INTO dishes (name, calories, protein, fat, taste, health_goal, allergens, price, category, floor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', sample_dishes)

    conn.commit()
    conn.close()

# 执行初始化（应用启动时自动执行一次）
init_db()

# ---------- 菜品数据读取（带缓存，支持楼层过滤） ----------
@st.cache_data(ttl=600)
def load_dishes_from_mysql(floor=None):
    conn = sqlite3.connect(DB_PATH)
    try:
        if floor and floor != "全部":
            query = "SELECT name, calories, protein, fat, taste, health_goal, allergens, price, category FROM dishes WHERE floor = ?"
            df = pd.read_sql(query, conn, params=(floor,))
        else:
            query = "SELECT name, calories, protein, fat, taste, health_goal, allergens, price, category FROM dishes"
            df = pd.read_sql(query, conn)
    finally:
        conn.close()
    return df

# ---------- 用户画像操作 ----------
def get_existing_users():
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM user_profiles ORDER BY user_id")
        rows = cursor.fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()

def load_user_profile(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # 使查询结果可像字典一样访问
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            profile = dict(row)
            if profile.get('extra_info') and isinstance(profile['extra_info'], str):
                profile['extra_info'] = json.loads(profile['extra_info'])
            return profile
        else:
            return {}
    finally:
        conn.close()

def save_user_profile(user_id, profile_dict):
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        # 检查用户是否存在
        cursor.execute("SELECT 1 FROM user_profiles WHERE user_id = ?", (user_id,))
        exists = cursor.fetchone()

        if exists:
            # 动态构建 UPDATE 语句
            set_clauses = []
            values = []
            for key, val in profile_dict.items():
                if val is not None and key != 'user_id':
                    set_clauses.append(f"{key} = ?")
                    if key == 'extra_info' and isinstance(val, dict):
                        val = json.dumps(val, ensure_ascii=False)
                    values.append(val)
            if set_clauses:
                values.append(user_id)
                sql = f"UPDATE user_profiles SET {', '.join(set_clauses)}, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?"
                cursor.execute(sql, values)
        else:
            fields = ['user_id']
            placeholders = ['?']
            values = [user_id]
            for key, val in profile_dict.items():
                if val is not None and key != 'user_id':
                    fields.append(key)
                    placeholders.append('?')
                    if key == 'extra_info' and isinstance(val, dict):
                        val = json.dumps(val, ensure_ascii=False)
                    values.append(val)
            sql = f"INSERT INTO user_profiles ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
            cursor.execute(sql, values)
        conn.commit()
    finally:
        conn.close()

def update_user_profile_with_llm(user_id, user_message, assistant_response, existing_profile):
    extract_prompt = f"""
你是一个信息提取助手。根据用户和助手的以下对话，提取用户提供的关于自己的个人信息：
- 身高（厘米，只取数字）
- 体重（公斤，只取数字）
- 饮食习惯（例如：爱吃辣、素食、喜欢甜食等）
- 爱好（例如：运动、看书、打游戏等）
- 运动频率（例如：每周三次、每天、从不等）

如果对话中没有提供上述某项信息，就输出 null。
只输出一个 JSON 对象，格式如下：
{{"height_cm": 数值或null, "weight_kg": 数值或null, "dietary_habit": "字符串或null", "hobby": "字符串或null", "exercise_frequency": "字符串或null"}}

对话内容：
用户：{user_message}
助手：{assistant_response}

注意：不要输出任何额外文字，只输出 JSON。
"""
    try:
        client_extra = OpenAI(api_key=key, base_url="https://open.bigmodel.cn/api/paas/v4")
        resp = client_extra.chat.completions.create(
            model="GLM-4-Flash-250414",
            messages=[{"role": "user", "content": extract_prompt}],
            temperature=0.1,
            max_tokens=300,
            extra_body={"thinking": {"type": "disabled"}}
        )
        content = resp.choices[0].message.content
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            new_info = json.loads(json_match.group())
            update_data = {k: v for k, v in new_info.items() if v is not None and v != "null"}
            if update_data:
                current = existing_profile.copy()
                current.update(update_data)
                allowed_fields = ['height_cm', 'weight_kg', 'dietary_habit', 'hobby', 'exercise_frequency']
                clean_data = {k: v for k, v in current.items() if k in allowed_fields and v is not None}
                if clean_data:
                    save_user_profile(user_id, clean_data)
                    st.toast(f"已更新用户画像：{', '.join(update_data.keys())}", icon="ℹ️")
    except Exception as e:
        print(f"提取用户信息失败: {e}")

# ---------- 用户对话历史持久化 ----------
def save_conversation_to_db(user_id, messages, timestamp=None):
    if not messages:
        return
    has_user_msg = any(msg["role"] == "user" for msg in messages)
    if not has_user_msg:
        return
    if timestamp is None:
        timestamp = datetime.datetime.now().strftime("%m-%d %H:%M")
    first_user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
    preview = first_user_msg[:20] + "..." if len(first_user_msg) > 20 else first_user_msg
    messages_json = json.dumps(messages, ensure_ascii=False)

    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        # 检查是否已存在完全相同的对话（避免重复保存）
        cursor.execute(
            "SELECT id FROM user_conversation_history WHERE user_id = ? AND messages = ? LIMIT 1",
            (user_id, messages_json)
        )
        if cursor.fetchone():
            return
        cursor.execute(
            "INSERT INTO user_conversation_history (user_id, timestamp, messages, preview) VALUES (?, ?, ?, ?)",
            (user_id, timestamp, messages_json, preview)
        )
        conn.commit()
    finally:
        conn.close()

def load_user_conversations(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, timestamp, preview, messages FROM user_conversation_history WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            conv = dict(row)
            conv['messages'] = json.loads(conv['messages'])
            result.append(conv)
        return result
    finally:
        conn.close()

def delete_all_conversations_of_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_conversation_history WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()

# ========== 增强版推荐函数 ==========
def recommend_by_kg(floor=None, health_goal=None, taste=None, allergens=None, price_pref=None, category=None):
    dishes_df = load_dishes_from_mysql(floor=floor)
    filtered = dishes_df.copy()

    if allergens:
        for a in allergens:
            if a != "无":
                filtered = filtered[~filtered['allergens'].str.contains(a)]

    if health_goal == '减脂':
        filtered = filtered[filtered['calories'] < 400]
        filtered = filtered[filtered['fat'] < 15]
        filtered = filtered.sort_values('calories')
    elif health_goal == '增肌':
        filtered = filtered[filtered['protein'] > 20]
        filtered = filtered.sort_values('protein', ascending=False)

    if taste and taste != "任意":
        filtered = filtered[filtered['taste'].str.contains(taste) | (filtered['taste'] == taste)]

    if category and category != "任意":
        filtered = filtered[filtered['category'] == category]

    if price_pref == '便宜':
        filtered = filtered.sort_values('price')

    if len(filtered) < 2 and (taste or category):
        filtered = dishes_df.copy()
        if allergens:
            for a in allergens:
                if a != "无":
                    filtered = filtered[~filtered['allergens'].str.contains(a)]
        if health_goal == '减脂':
            filtered = filtered[filtered['calories'] < 400]
        elif health_goal == '增肌':
            filtered = filtered[filtered['protein'] > 20]
        if category and category != "任意":
            filtered = filtered[filtered['category'] == category]
        if price_pref == '便宜':
            filtered = filtered.sort_values('price')

    top_dishes = filtered.head(3).to_dict('records')
    for dish in top_dishes:
        reasons = []
        if health_goal == '减脂':
            reasons.append(f"热量仅{dish['calories']}千卡，低脂健康")
        elif health_goal == '增肌':
            reasons.append(f"含{dish['protein']}g蛋白质，有助增肌")
        if price_pref == '便宜':
            reasons.append(f"仅{dish['price']}元，价格实惠")
        if taste and taste != "任意" and taste in dish['taste']:
            reasons.append(f"{dish['taste']}口味，符合您的偏好")
        if category and category != "任意" and dish['category'] == category:
            reasons.append(f"属于{dish['category']}类")
        if not reasons:
            reasons.append("食材新鲜，营养均衡")
        dish['reason'] = "；".join(reasons)
    return top_dishes

# ========== 根据用户消息提取食物关键词 ==========
def extract_food_keywords(text):
    keywords = ["海鲜", "鱼", "虾", "蟹", "贝", "肉", "鸡", "鸭", "牛肉", "猪肉", "素菜", "蔬菜", "青菜", "豆腐"]
    found = [kw for kw in keywords if kw in text]
    return found[0] if found else "这类菜品"

# ========== 生成无结果时的友好提示 ==========
def no_result_message(user_prompt, current_floor):
    floor_display = current_floor if current_floor != "全部" else "当前楼层"
    keyword = extract_food_keywords(user_prompt)
    return f"😢 抱歉，{floor_display} 没有您想吃的 **{keyword}**。您可以试试切换到其他楼层，或者调整一下口味/健康需求。"

# ========== 尝试从用户消息中自动构造推荐请求（fallback） ==========
def auto_construct_recommend(user_message):
    category = "任意"
    if "素菜" in user_message or "蔬菜" in user_message or "青菜" in user_message:
        category = "素菜"
    elif "鱼" in user_message:
        category = "鱼类"
    elif "肉" in user_message:
        category = "肉类"
    taste = "任意"
    if "辣" in user_message:
        taste = "辣"
    elif "清淡" in user_message:
        taste = "清淡"
    health_goal = "任意"
    if "减脂" in user_message or "减肥" in user_message:
        health_goal = "减脂"
    elif "增肌" in user_message:
        health_goal = "增肌"
    price_pref = "任意"
    if "便宜" in user_message:
        price_pref = "便宜"
    allergens = "无"
    return f"[RECOMMEND: {health_goal}, {taste}, {allergens}, {price_pref}, {category}]"

# ========== OpenAI 客户端 ==========
client = OpenAI(api_key=key, base_url="https://open.bigmodel.cn/api/paas/v4")

# ========== 系统提示词 ==========
BASE_SYSTEM_PROMPT = """
你是食堂智选侠，一个专业的食堂推荐助手。你的任务是：
1. 根据用户的口味偏好（如辣、清淡、酸甜）、健康目标（减脂/增肌/常规）、过敏原、价格要求（便宜/任意）、菜品类别（素菜/鱼类/肉类/任意），推荐合适的菜品。
2. **重要**：只要用户表达了想吃某种菜品或需要推荐（包括说“想吃海鲜”、“想吃肉”等），你必须在回复中按以下格式输出推荐请求，不得自行编造任何菜品名称或菜谱：
   [RECOMMEND: 健康目标, 口味, 过敏原列表, 价格要求, 菜品类别]
   例如：
   - 用户说“我想减脂，不吃大豆，想吃便宜的” → 输出 [RECOMMEND: 减脂, 任意, 大豆, 便宜, 任意]
   - 用户说“今天想吃辣的，高蛋白” → 输出 [RECOMMEND: 增肌, 辣, 无, 任意, 任意]
   - 用户说“我对鱼类过敏，想吃素菜” → 输出 [RECOMMEND: 常规, 任意, 鱼类, 任意, 素菜]
   - 用户说“想吃青菜/蔬菜” → 输出 [RECOMMEND: 任意, 任意, 无, 任意, 素菜]
   - 用户说“我想吃海鲜” → 输出 [RECOMMEND: 任意, 任意, 无, 任意, 鱼类]   （注意：海鲜归类为鱼类）
   - 用户说“想吃肉” → 输出 [RECOMMEND: 任意, 任意, 无, 任意, 肉类]
   - 用户说“便宜一点” → 输出 [RECOMMEND: 任意, 任意, 无, 便宜, 任意]
   注意：价格要求可以是“便宜”或“任意”；菜品类别可以是“素菜”、“鱼类”、“肉类”或“任意”。
3. 如果用户只是闲聊或询问其他问题（如食堂位置、营业时间），请正常回答，不需要输出推荐请求。
4. 回答要简洁友好，可以适当添加推荐理由。每次最多推荐3道菜。
5. 用户说想吃什么就直接输出推荐请求，不要过多询问。
"""

def build_system_prompt(user_profile, current_floor):
    profile_text = ""
    if user_profile:
        info = []
        if user_profile.get('height_cm'):
            info.append(f"身高 {user_profile['height_cm']} cm")
        if user_profile.get('weight_kg'):
            info.append(f"体重 {user_profile['weight_kg']} kg")
        if user_profile.get('dietary_habit'):
            info.append(f"饮食习惯：{user_profile['dietary_habit']}")
        if user_profile.get('hobby'):
            info.append(f"爱好：{user_profile['hobby']}")
        if user_profile.get('exercise_frequency'):
            info.append(f"运动频率：{user_profile['exercise_frequency']}")
        if info:
            profile_text = f"\n你了解到的当前用户信息：{'; '.join(info)}。"
    floor_text = f"\n当前用户正在 **{current_floor} 食堂** 就餐，请只推荐该楼层的菜品。"
    return BASE_SYSTEM_PROMPT + profile_text + floor_text

# ========== Streamlit 界面初始化 ==========
if "current_user_id" not in st.session_state:
    st.session_state.current_user_id = "guest"
if "messages" not in st.session_state:
    st.session_state.messages = []
if "current_floor" not in st.session_state:
    st.session_state.current_floor = "1F"

def save_current_conversation():
    if st.session_state.messages:
        save_conversation_to_db(st.session_state.current_user_id, st.session_state.messages)

# ========== 侧边栏 ==========
with st.sidebar:
    st.header("👤 用户")
    existing_users = get_existing_users()
    if not existing_users:
        existing_users = ["guest"]
    selected_user = st.selectbox(
        "选择或新建用户",
        options=existing_users + ["➕ 新建用户..."],
        index=0 if st.session_state.current_user_id in existing_users else 0
    )
    if selected_user == "➕ 新建用户...":
        new_user_id = st.text_input("输入新用户名", key="new_user_id")
        if st.button("创建用户", use_container_width=True):
            if new_user_id and new_user_id.strip():
                save_current_conversation()
                save_user_profile(new_user_id.strip(), {})
                st.session_state.current_user_id = new_user_id.strip()
                st.session_state.messages = []
                st.rerun()
    else:
        if st.session_state.current_user_id != selected_user:
            save_current_conversation()
            st.session_state.current_user_id = selected_user
            st.session_state.messages = []
            st.rerun()

    profile = load_user_profile(st.session_state.current_user_id)
    if profile:
        with st.expander("📋 我的健康档案"):
            if profile.get('height_cm'): st.write(f"身高：{profile['height_cm']} cm")
            if profile.get('weight_kg'): st.write(f"体重：{profile['weight_kg']} kg")
            if profile.get('dietary_habit'): st.write(f"饮食习惯：{profile['dietary_habit']}")
            if profile.get('hobby'): st.write(f"爱好：{profile['hobby']}")
            if profile.get('exercise_frequency'): st.write(f"运动频率：{profile['exercise_frequency']}")
    else:
        st.caption("暂无画像，我会从聊天中学习你的信息。")

    st.divider()

    st.header("🏢 食堂楼层")
    floor_options = ["1F", "2F", "3F", "全部"]
    selected_floor = st.selectbox(
        "选择楼层",
        options=floor_options,
        index=floor_options.index(st.session_state.current_floor) if st.session_state.current_floor in floor_options else 0
    )
    if selected_floor != st.session_state.current_floor:
        st.session_state.current_floor = selected_floor
        st.rerun()

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("➕ 新建对话", use_container_width=True):
            save_current_conversation()
            st.session_state.messages = []
            st.rerun()
    with col2:
        with st.popover("🗑️ 清空历史", use_container_width=True):
            st.warning(f"⚠️ 确定要清空用户 **{st.session_state.current_user_id}** 的所有历史对话吗？此操作不可恢复。")
            if st.button("确认清空", type="primary", use_container_width=True):
                count = delete_all_conversations_of_user(st.session_state.current_user_id)
                st.toast(f"已清空 {count} 条历史对话", icon="🗑️")
                st.rerun()

    st.subheader("历史对话")
    conv_list = load_user_conversations(st.session_state.current_user_id)
    if conv_list:
        for conv in conv_list:
            label = f"{conv['timestamp']} - {conv['preview']}"
            if st.button(label, key=f"hist_{conv['id']}", use_container_width=True):
                st.session_state.messages = conv['messages']
                st.rerun()
    else:
        st.caption("暂无历史对话")

# ========== 主界面 ==========
st.title("欢迎使用食堂智选侠")
st.subheader(f"-- 当前楼层：{st.session_state.current_floor} --")
with st.container():
    st.info("💡 我会记住你的身高、体重、饮食爱好等信息，为你提供更贴心的推荐。")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ========== 处理用户输入 ==========
if prompt := st.chat_input("描述你的口味，或告诉我关于你的健康信息..."):
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        with placeholder.container():
            with st.spinner("🤔 AI正在分析..."):
                try:
                    user_profile = load_user_profile(st.session_state.current_user_id)
                    system_prompt = build_system_prompt(user_profile, st.session_state.current_floor)
                    current_messages = [{"role": "system", "content": system_prompt}] + st.session_state.messages + [{"role": "user", "content": prompt}]

                    response = client.chat.completions.create(
                        model="GLM-4-Flash-250414",
                        messages=current_messages,
                        temperature=0.7,
                        max_tokens=500,
                        timeout=30.0,
                        extra_body={"thinking": {"type": "disabled"}}
                    )
                    raw_answer = response.choices[0].message.content

                    recommend_pattern = r'\[RECOMMEND:\s*([^,]+),\s*([^,]+),\s*([^,]+),\s*([^,]+),\s*([^\]]+)\]'
                    match = re.search(recommend_pattern, raw_answer)

                    food_keywords = ["吃", "想", "来", "要", "海鲜", "鱼", "肉", "素", "菜", "辣", "清淡", "减脂", "增肌", "便宜"]
                    if not match and any(kw in prompt for kw in food_keywords):
                        auto_rec = auto_construct_recommend(prompt)
                        raw_answer = raw_answer + "\n" + auto_rec
                        match = re.search(recommend_pattern, raw_answer)

                    if match:
                        health_goal = match.group(1).strip()
                        taste = match.group(2).strip()
                        allergens_str = match.group(3).strip()
                        price_pref = match.group(4).strip()
                        category = match.group(5).strip()

                        if allergens_str == "无" or allergens_str == "无过敏":
                            allergens = []
                        else:
                            allergens = [a.strip() for a in allergens_str.split(',')]

                        health_goal = health_goal if health_goal != "任意" else None
                        taste = taste if taste != "任意" else None
                        price_pref = price_pref if price_pref in ["便宜"] else None
                        category = category if category in ["素菜", "鱼类", "肉类"] else None

                        rec_list = recommend_by_kg(
                            floor=st.session_state.current_floor if st.session_state.current_floor != "全部" else None,
                            health_goal=health_goal,
                            taste=taste,
                            allergens=allergens,
                            price_pref=price_pref,
                            category=category
                        )

                        if rec_list:
                            rec_text = "🍽️ 根据您的需求，为您推荐：\n\n"
                            for idx, dish in enumerate(rec_list, 1):
                                rec_text += f"{idx}. **{dish['name']}**\n"
                                rec_text += f"   - 热量：{dish['calories']}千卡 | 蛋白质：{dish['protein']}g | 口味：{dish['taste']} | 价格：{dish['price']}元\n"
                                rec_text += f"   - 💡 推荐理由：{dish['reason']}\n\n"
                            final_answer = re.sub(recommend_pattern, rec_text, raw_answer)
                        else:
                            hint = no_result_message(prompt, st.session_state.current_floor)
                            final_answer = re.sub(recommend_pattern, hint, raw_answer)
                    else:
                        final_answer = raw_answer

                except Exception as e:
                    final_answer = f"❌ 请求失败，请稍后重试。\n错误详情：{e}"

        placeholder.markdown(final_answer)

    st.session_state.messages.append({"role": "user", "content": prompt})
    st.session_state.messages.append({"role": "assistant", "content": final_answer})

    keywords = ["身高", "体重", "kg", "cm", "喜欢", "习惯", "爱好", "运动", "不吃", "爱吃", "素食", "健身"]
    if any(kw in prompt for kw in keywords):
        threading.Thread(
            target=update_user_profile_with_llm,
            args=(st.session_state.current_user_id, prompt, final_answer, user_profile),
            daemon=True
        ).start()

    st.rerun()
    time.sleep(1)