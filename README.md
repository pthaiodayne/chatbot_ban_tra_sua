# Milk Tea Bot

Bot Telegram cho quán đồ uống, dùng:
- FastAPI
- Telegram Bot API (webhook)
- OpenAI API (xử lý tin nhắn tự nhiên)
- SQLite + SQLAlchemy
- Menu đọc từ `data/menu.csv`

## Điểm đã chỉnh theo file Menu.csv thật
- Đọc đúng schema: `category,item_id,name,description,price_m,price_l,available`
- Tách riêng đồ uống và topping
- Chỉ cho phép gọi món từ các category khác `Topping`
- Tính giá theo size `M/L`
- Tính topping theo chính giá trong file CSV
- Thêm lệnh `/toppings`

## Cài đặt
```bash
pip install -r requirements.txt
```

Tạo file `.env` từ `.env.example`, rồi điền token bot, URL public và OpenAI API key nếu muốn bot hiểu tin nhắn tự nhiên. Nếu muốn bot báo đơn sang Telegram của người bán, cấu hình thêm `SELLER_CHAT_ID`.

## Chạy app local sau khi đã cài đặt và cấu hình `.env`:
```bash
uvicorn app.main:app --reload
```

## Lệnh bot
- `/start`
- `/menu`
- `/toppings`
- `/add <tên món>|<size>|<số lượng>|<topping1,topping2>`
- `/cart`
- `/name <tên khách>`
- `/phone <số điện thoại của bạn>`
- `/address <địa chỉ>`
- `/note <ghi chú>`
- `/checkout`
- `/pay`
- `/confirm_pay`
- `/summary`

## Ví dụ
```text
/menu
/toppings
/add Trà Sữa Truyền Thống|L|2|Kem Tươi,Trân Châu Đen
/name Nguyễn Văn A
/phone 123456789
/address Dĩ An, Bình Dương
/checkout
/pay
/confirm_pay
```

Ví dụ tin nhắn tự nhiên khi đã cấu hình `OPENAI_API_KEY`:
```text
Cho mình 2 trà sữa truyền thống size L thêm kem tươi
Mình tên An, số điện thoại của mình là 09..., địa chỉ 123 Nguyễn Trãi
Menu hôm nay có gì?
```

## Thông báo cho người bán
- Cấu hình `SELLER_CHAT_ID` trong `.env`
- Khi khách `/confirm_pay`, bot sẽ gửi thêm thông báo đơn đã thanh toán
