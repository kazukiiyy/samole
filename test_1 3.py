from flask import Flask, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import yfinance as yf
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///simulator.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your_secret_key'
db = SQLAlchemy(app)

# --- モデル定義 ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    cash_balance = db.Column(db.Float, default=1000000)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticker_symbol = db.Column(db.String(20), unique=True, nullable=False)
    company_name = db.Column(db.String(80), nullable=False)
    value_tags = db.Column(db.String(120))

class Holding(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    stock_id = db.Column(db.Integer, db.ForeignKey('stock.id'), nullable=False)
    quantity = db.Column(db.Integer, default=0)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    stock_id = db.Column(db.Integer, db.ForeignKey('stock.id'), nullable=False)
    quantity = db.Column(db.Integer)
    price = db.Column(db.Float)
    type = db.Column(db.String(10))  # 'buy' or 'sell'
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# --- 企業・キャラクター作品モデル ---
class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    ticker_symbol = db.Column(db.String(20), unique=True, nullable=True)

class CharacterWork(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), unique=True, nullable=False)

class WorkCompany(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    work_id = db.Column(db.Integer, db.ForeignKey('character_work.id'), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)


# --- 株価取得関数 ---
def get_current_price(ticker_symbol):
    try:
        stock = yf.Ticker(ticker_symbol)
        todays_data = stock.history(period='1d')
        if todays_data.empty:
            print(f"警告: {ticker_symbol}の株価データを取得できませんでした（データが空です）。")
            return None
        return float(todays_data['Close'].iloc[0])
    except Exception as e:
        print(f"エラー: {ticker_symbol}の株価取得中に問題が発生しました: {e}")
        return None

# --- ユーザー登録API ---
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already exists'}), 400
    user = User(username=username)
    user.set_password(password)
    user.cash_balance = 1000000
    db.session.add(user)
    db.session.commit()
    return jsonify({'message': 'User registered successfully'})

# --- ユーザーログインAPI ---
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        return jsonify({'error': 'Invalid username or password'}), 401
    session['user_id'] = user.id
    return jsonify({'message': 'Login successful', 'user_id': user.id})

# --- ログアウトAPI ---
@app.route('/logout', methods=['POST'])
def logout():
    session.pop('user_id', None)
    return jsonify({'message': 'Logged out'})

# --- ポートフォリオ取得API ---
@app.route('/portfolio', methods=['GET'])
def get_portfolio():
    user_id = session.get('user_id', 1)
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    holdings = Holding.query.filter_by(user_id=user_id).all()
    result = {
        'username': user.username,
        'cash_balance': user.cash_balance,
        'holdings': [
            {
                'ticker': Stock.query.get(h.stock_id).ticker_symbol,
                'company_name': Stock.query.get(h.stock_id).company_name,
                'quantity': h.quantity
            } for h in holdings if h.quantity > 0
        ]
    }
    return jsonify(result)

# --- 売買API ---
@app.route('/trade', methods=['POST'])
def trade_stock():
    user_id = session.get('user_id', 1)
    data = request.json
    ticker = data.get('ticker')
    quantity = int(data.get('quantity', 0))
    trade_type = data.get('type')
    if not all([ticker, quantity > 0, trade_type in ['buy', 'sell']]):
        return jsonify({'error': 'Invalid request parameters'}), 400
    user = User.query.get(user_id)
    stock = Stock.query.filter_by(ticker_symbol=ticker).first()
    if not user or not stock:
        return jsonify({'error': 'User or Stock not found'}), 404
    price = get_current_price(ticker)
    if price is None:
        return jsonify({'error': f'Could not fetch price for {ticker}'}), 500
    holding = Holding.query.filter_by(user_id=user_id, stock_id=stock.id).first()
    if trade_type == 'buy':
        cost = price * quantity
        if user.cash_balance < cost:
            return jsonify({'error': 'Insufficient funds'}), 400
        user.cash_balance -= cost
        if holding:
            holding.quantity += quantity
        else:
            holding = Holding(user_id=user_id, stock_id=stock.id, quantity=quantity)
            db.session.add(holding)
        message = f'Successfully bought {quantity} shares of {ticker}'
    elif trade_type == 'sell':
        if not holding or holding.quantity < quantity:
            return jsonify({'error': 'Not enough holdings to sell'}), 400
        proceeds = price * quantity
        user.cash_balance += proceeds
        holding.quantity -= quantity
        message = f'Successfully sold {quantity} shares of {ticker}'
    transaction = Transaction(user_id=user_id, stock_id=stock.id, quantity=quantity, price=price, type=trade_type)
    db.session.add(transaction)
    db.session.commit()
    return jsonify({'message': message, 'cash_balance': user.cash_balance})

# --- キャラクター作品から関連企業推薦API ---
@app.route('/recommend_companies', methods=['POST'])
def recommend_companies():
    data = request.json
    selected_titles = data.get('titles', [])  # 例: ["アイドルマスター", "あつまれ どうぶつの森"]
    companies = set()
    for title in selected_titles:
        work = CharacterWork.query.filter_by(title=title).first()
        if work:
            relations = WorkCompany.query.filter_by(work_id=work.id).all()
            for rel in relations:
                company = Company.query.get(rel.company_id)
                if company:
                    companies.add((company.name, company.ticker_symbol))
    result = [
        {"name": name, "ticker_symbol": ticker}
        for name, ticker in companies
    ]
    return jsonify(result)

# --- 企業株価一覧API ---
@app.route('/company_prices', methods=['GET'])
def company_prices():
    companies = Company.query.all()
    prices = []
    for company in companies:
        if company.ticker_symbol:
            price = get_current_price(company.ticker_symbol)
        else:
            price = None
        prices.append({
            "name": company.name,
            "ticker_symbol": company.ticker_symbol,
            "price": price
        })
    return jsonify(prices)

# --- データベース初期化コマンド（全件追加例） ---
@app.cli.command("init-db")
def init_db_command():
    db.drop_all()
    db.create_all()
    # ユーザー・株式サンプル
    user = User(id=1, username='testuser')
    user.set_password('testpass')
    db.session.add(user)
    stock1 = Stock(ticker_symbol='7974.T', company_name='任天堂', value_tags='#ゲーム,#エンタメ')
    stock2 = Stock(ticker_symbol='6758.T', company_name='ソニーグループ', value_tags='#ゲーム,#エンタメ,#技術')
    db.session.add(stock1)
    db.session.add(stock2)

    # --- 企業リスト（全件追加例） ---
    company_data = [
        # 企業名, ticker_symbol（上場企業のみ。非上場はNone）
        ('任天堂', '7974.T'),
        ('ソニーグループ', '6758.T'),
        ('バンダイナムコエンターテインメント', '7832.T'),
        ('東映アニメーション', '4816.T'),
        ('東映', '9605.T'),
        ('東宝', '9602.T'),
        ('KADOKAWA', '9468.T'),
        ('タカラトミー', '7867.T'),
        ('スクウェア・エニックス', '9684.T'),
        ('セガサミーホールディングス', '6460.T'),
        ('カプコン', '9697.T'),
        ('KONAMIグループ', '9766.T'),
        ('ブシロード', '7803.T'),
        ('マーベラス', '7844.T'),
        ('テレビ朝日ホールディングス', '9409.T'),
        ('日本テレビホールディングス', '9404.T'),
        ('フジ・メディア・ホールディングス', '4676.T'),
        ('TBSホールディングス', '9401.T'),
        ('テレビ東京ホールディングス', '9413.T'),
        ('博報堂DYホールディングス', '2433.T'),
        ('ウォルト・ディズニー・カンパニー','DIS'),
        ('ユニバーサル・スタジオ','CMCSA'),
        ('ワーナー・ブラザース・ディスカバリー','WBD'),
        ('Netflix','NFLX'),
        ('ソニー・ピクチャーズ','SONY'),
        ('マテル','MAT'),
        ('IGポート','3791.T'),
        ('サイバーエージェント','4751.T'),
        ('円谷フィールズホールディングス','2767.T'),
        ('サンリオ','8136.T'),
        ('TOPPANホールディングス','7911.T'),
        ('電通グループ','4324.T'),
        ('東北新社','2329.T'),
        ('エイベックス','7860.T'),
        ('KLab','3656.T'),
        ('相鉄ホールディングス','9003.T'),
        ('ロッテ','None'),
        ('KRAFTON','KRX:259960'),
        ('ベネッセホールディングス','9783.T'),
        ('エポック社','None'),
        ('FOOD & LIFE COMPANIES','3563.T'),
        ('NHK','None'),
        ('マーベラス','7844.T'),
        # ...（全リストをここに追加してください）
    ]
    company_objs = []
    for name, ticker in company_data:
        company_objs.append(Company(name=name, ticker_symbol=ticker))
    db.session.add_all(company_objs)
    db.session.commit()

    # --- キャラクター作品リスト（全件追加例） ---
    work_titles = [
        'アイドルマスター',
        'あつまれ どうぶつの森',
        'アナと雪の女王',
        'あらいぐまラスカル',
        'ウォーリーをさがせ！',
        '宇宙戦艦ヤマト',
        'ウマ娘 プリティーダービー',
        'ウルトラマン',
        'エヴァンゲリオン',
        '炎炎ノ消防隊',
        'お買いものパンダ',
        'おそ松さん',
        'ガチャピン・ムック',
        '家庭教師ヒットマンREBORN!',
        'カピバラさん',
        '仮面ライダー',
        'きかんしゃトーマス',
        '機動戦士ガンダム',
        '鬼滅の刃',
        'キャプテン翼',
        'キングダム',
        'キン肉マン',
        'ぐでたま',
        'くまのがっこう',
        'くまのプーさん',
        'くまモン',
        'クレヨンしんちゃん',
        'クロミ',
        '黒子のバスケ',
        'ゲゲゲの鬼太郎',
        'コアラのマーチ',
        'ゴジラ',
        'こちら葛飾区亀有公園前派出所',
        'サザエさん',
        'シティーハンター',
        'シナモロール',
        'しまじろう',
        '呪術廻戦',
        'シルバニアファミリー',
        '進撃の巨人',
        'スーパーマリオブラザーズ',
        'スター・ウォーズ',
        'スヌーピー',
        'スパイダーマン',
        'SPY×FAMILY',
        'すみっコぐらし',
        '聖闘士星矢',
        'セサミストリート',
        'ソードアート・オンライン',
        'それいけ！アンパンマン',
        'TIGER & BUNNY',
        'だっこずし',
        'ちいかわ',
        'チコちゃんに叱られる！',
        'ちびまる子ちゃん',
        'ディズニープリンセス',
        'テニスの王子様',
        '天才バカボン',
        '転生したらスライムだった件',
        'トイ・ストーリー',
        '東京リベンジャーズ',
        '刀剣乱舞',
        'となりのトトロ',
        'トムとジェリー',
        'ドラえもん',
        'ドラゴンクエスト',
        'ドラゴンボール',
        '夏目友人帳',
        '初音ミク',
        'ハローキティ',
        'バンドリ！ ガールズバンドパーティ！',
        'ピーターラビット',
        '美少女戦士セーラームーン',
        'ヒプノシスマイク',
        'ファイナルファンタジー',
        'PUI PUI モルカー',
        'プリキュア',
        'ベイブレード',
        'ベルサイユのばら',
        'ポケットモンスター',
        '僕のヒーローアカデミア',
        '星のカービィ',
        'ぼのぼの',
        'マーシャとくま',
        'マイメロディ',
        'ミッキー＆フレンズ',
        'ミッフィー',
        'ミニオン',
        'ムーミン',
        '名探偵コナン',
        '遊戯王',
        '妖怪人間ベム',
        '弱虫ペダル',
        'ラブライブ！',
        'リカちゃん',
        'リラックマ',
        'ルパン三世',
        'ワンパンマン',
        'ONE PIECE',
        # ...（全リストをここに追加してください）
    ]
    work_objs = []
    for title in work_titles:
        work_objs.append(CharacterWork(title=title))
    db.session.add_all(work_objs)
    db.session.commit()

    # --- 作品と企業の関連付け（全件追加例） ---
    def get_company_id(name):
        return Company.query.filter_by(name=name).first().id

    def get_work_id(title):
        return CharacterWork.query.filter_by(title=title).first().id

    relations_data = [
        # 例: ('アイドルマスター', ['バンダイナムコエンターテインメント', 'A-1 Pictures', 'Cygames', 'バンダイナムコミュージックライブ'])
        ('アイドルマスター', ['バンダイナムコエンターテインメント','サイバーエージェント']),
        ('あつまれ どうぶつの森', ['任天堂']),
        ('アナと雪の女王', ['ウォルト・ディズニー・カンパニー']),
        ('あらいぐまラスカル', ['フジ・メディア・ホールディングス']),
        ('ウォーリーをさがせ！',['TOPPANホールディングス']),
        ('宇宙戦艦ヤマト',['日本テレビホールディングス' ,'東北新社','バンダイナムコエンターテインメント']),
        ('ウマ娘 プリティーダービー',['サイバーエージェント','東宝','バンダイナムコエンターテインメント']),
        ('ウルトラマン',['円谷フィールズホールディングス','バンダイナムコエンターテインメント']),
        ('エヴァンゲリオン',['セガサミーホールディングス','バンダイナムコエンターテインメント']),
        ('炎炎ノ消防隊',['TBSホールディングス']),
        ('お買いものパンダ',['楽天グループ']),
        ('おそ松さん',['テレビ東京ホールディングス','エイベックス']),
        ('ガチャピン・ムック',['フジ・メディア・ホールディングス']),
        ('家庭教師ヒットマンREBORN!',['テレビ東京ホールディングス','電通グループ']),
        ('カピバラさん',['バンダイナムコエンターテインメント']),
        ('仮面ライダー',['東映','テレビ朝日ホールディングス','バンダイナムコエンターテインメント']),
        ('きかんしゃトーマス',['マテル']),
        ('機動戦士ガンダム',['バンダイナムコエンターテインメント']),
        ('鬼滅の刃',['ソニーグループ']),
        ('キャプテン翼',['テレビ東京ホールディングス','KLab']),
        ('キングダム',['KONAMIグループ','東宝']),
        ('キン肉マン',['東映アニメーション','日本テレビホールディングス']),
        ('ぐでたま',['サンリオ']),
        ('くまのがっこう',['バンダイナムコエンターテインメント']),
        ('くまのプーさん',['ウォルト・ディズニー・カンパニー']),
        ('くまモン',['相鉄ホールディングス']),
        ('クレヨンしんちゃん',['テレビ朝日ホールディングス']),
        ('クロミ',['サンリオ']),
        ('黒子のバスケ',['IGポート','バンダイナムコエンターテインメント']),
        ('ゲゲゲの鬼太郎',['フジ・メディア・ホールディングス','東映アニメーション']),
        ('コアラのマーチ',['ロッテ']),
        ('ゴジラ',['東宝']),
        ('こちら葛飾区亀有公園前派出所',['KRAFTON']),
        ('サザエさん',['フジ・メディア・ホールディングス']),
        ('シティーハンター',['バンダイナムコエンターテインメント','ソニーグループ']),
        ('シナモロール',['サンリオ']),
        ('しまじろう',['ベネッセホールディングス']),
        ('呪術廻戦',['東宝','バンダイナムコエンターテインメント']),
        ('シルバニアファミリー',['エポック社']),
        ('進撃の巨人',['IGポート','フジ・メディア・ホールディングス']),
        ('スーパーマリオブラザーズ',['任天堂']),
        ('スター・ウォーズ',['ウォルト・ディズニー・カンパニー']),
        ('スヌーピー',['ソニーグループ']),
        ('スパイダーマン',['ウォルト・ディズニー・カンパニー','ソニー・ピクチャーズ']),
        ('SPY×FAMILY',['テレビ東京ホールディングス','東宝']),
        ('すみっコぐらし',['バンダイナムコエンターテインメント']),
        ('聖闘士星矢',['東映アニメーション','テレビ朝日ホールディングス']),
        ('セサミストリート',['ソニーグループ']),
        ('ソードアート・オンライン',['ソニーグループ','バンダイナムコエンターテインメント']),
        ('それいけ！アンパンマン',['セガサミーホールディングス','日本テレビホールディングス','TOPPANホールディングス']),
        ('TIGER & BUNNY',['バンダイナムコエンターテインメント']),
        ('だっこずし',['FOOD & LIFE COMPANIES']),
        ('ちいかわ',['フジ・メディア・ホールディングス']),
        ('チコちゃんに叱られる！',['NHK']),
        ('ちびまる子ちゃん',['フジ・メディア・ホールディングス','博報堂DYホールディングス']),
        ('ディズニープリンセス',['ウォルト・ディズニー・カンパニー']),
        ('テニスの王子様',['マーベラス','ブシロード']),
        ('天才バカボン',['セガサミーホールディングス']),
        ('転生したらスライムだった件',['バンダイナムコエンターテインメント']),
        ('トイ・ストーリー',['ウォルト・ディズニー・カンパニー']),
        ('東京リベンジャーズ',['フジ・メディア・ホールディングス']),
        ('刀剣乱舞',['サイバーエージェント','マーベラス']),
        ('となりのトトロ',['東宝']),
        ('トムとジェリー',['ワーナー・ブラザース・ディスカバリー']),
        ('ドラえもん',['テレビ朝日ホールディングス']),
        ('ドラゴンクエスト',['スクウェア・エニックス','東映アニメーション']),
        ('ドラゴンボール',['バンダイナムコエンターテインメント','東映アニメーション','フジ・メディア・ホールディングス']),
        ('夏目友人帳',['ソニーグループ']),
        ('初音ミク',['セガサミーホールディングス']),
        ('ハローキティ',['サンリオ']),
        ('バンドリ！ ガールズバンドパーティ！',['ブシロード','サイバーエージェント']),
        ('ピーターラビット',['ソニーグループ']),
        ('美少女戦士セーラームーン',['東映アニメーション']),
        ('')

        # ...（全リストをここに追加してください。作品名と企業名の組み合わせで）
    ]
    relation_objs = []
    for work_title, company_names in relations_data:
        work_id = get_work_id(work_title)
        for company_name in company_names:
            company_id = get_company_id(company_name)
            relation_objs.append(WorkCompany(work_id=work_id, company_id=company_id))
    db.session.add_all(relation_objs)
    db.session.commit()
    print("データベースを初期化し、キャラクター作品・企業・関連付けを全件投入しました。")

if __name__ == '__main__':
    app.run(debug=True)