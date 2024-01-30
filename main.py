import config

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.utils import executor

from openai import OpenAI


bot = Bot(token=config.TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
client = OpenAI(organization=config.ORGANIZATION)


class Form(StatesGroup):
    location = State()
    checklist_item = State()
    comment = State()
    photo = State()


@dp.message_handler(commands=['start'], state='*')
async def send_welcome(message: types.Message):
    await message.reply("Привіт! Почнімо працювати.")
    await choose_location(message)


@dp.message_handler(state=Form.location)
async def choose_location(message: types.Message):
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    locations = [f"Локація {i}" for i in range(1, config.LOCATIONS_NUM + 1)]
    keyboard.add(*locations)
    await message.answer("Оберіть локацію:", reply_markup=keyboard)
    await Form.checklist_item.set()


@dp.message_handler(lambda message: message.text.startswith("Локація"),
                    state=Form.checklist_item)
async def process_location(message: types.Message, state: FSMContext):
    await state.update_data(location=message.text)
    await message.answer("Локація вибрана: " + message.text,
                         reply_markup=ReplyKeyboardRemove())
    await next_checklist_item(message, state, 1)


async def next_checklist_item(message: types.Message, state: FSMContext,
                              item_number: int):
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add("Все чисто", "Залишити коментар")
    await Form.checklist_item.set()
    await state.update_data(current_item=item_number)
    await message.answer(
        f"Чек-лист пункт {item_number}: Все чисто або залишити коментар?",
        reply_markup=keyboard)


@dp.message_handler(
    lambda message: message.text in ["Все чисто", "Залишити коментар"],
    state=Form.checklist_item)
async def process_checklist_item(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    item_number = user_data['current_item']
    if message.text == "Залишити коментар":
        await state.update_data(
            {f'checklist_item_{item_number}': 'Залишити коментар'})
        await Form.comment.set()
        await message.answer("Будь ласка, введіть ваш коментар:",
                             reply_markup=ReplyKeyboardRemove())
    else:
        await state.update_data({f'checklist_item_{item_number}': 'Все чисто'})
        if item_number < config.CHECK_LIST_NUM:
            await next_checklist_item(message, state, item_number + 1)
        else:
            await finish_checklist(message, state)


@dp.message_handler(state=Form.comment)
async def process_comment(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    item_number = user_data['current_item']
    await state.update_data({f'comment_item_{item_number}': message.text})
    await Form.photo.set()
    await message.answer(
        "Будь ласка, завантажте фотографію для цього коментаря, "
        "або надішліть будь-яке текстове повідомлення, щоб пропустити.")


@dp.message_handler(content_types=['text'], state=Form.photo)
async def skip_photo(message: types.Message, state: FSMContext):
    await process_checklist_item(message, state)


@dp.message_handler(content_types=['photo'], state=Form.photo)
async def process_photo(message: types.Message, state: FSMContext):
    photo_file = await bot.get_file(message.photo[-1].file_id)
    photo_url = (
        f"https://api.telegram.org/file/bot{config.TOKEN}{photo_file.file_path}"
    )
    user_data = await state.get_data()
    item_number = user_data['current_item']
    photos = user_data.get('photos', {})
    photos[f'photo_item_{item_number}'] = photo_url
    await state.update_data(photos=photos)

    await process_checklist_item(message, state)


async def finish_checklist(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    report = generate_report(user_data)
    photo_urls = [url for key, url in user_data.items() if
                  key.startswith('photo_item_')]
    analyzed_report = await analyze_report(report, photo_urls=photo_urls)

    if analyzed_report:
        await message.answer("Аналізований звіт: " + analyzed_report)
    else:
        await message.answer("Не вдалося проаналізувати звіт.")

    await state.finish()  # Очищення даних стану
    await choose_location(message)  # Новий вибір локації


def generate_report(data) -> str:
    report = f"Локація: {data['location']}\n"
    photos = data.get('photos', {})
    for i in range(1, 6):
        report += (f"Чек-лист пункт {i}:"
                   f" {data.get(f'checklist_item_{i}', 'Не вказано')}\n")
        if f'comment_item_{i}' in data:
            report += f"Коментар: {data[f'comment_item_{i}']}\n"
        if f'photo_item_{i}' in photos:
            report += f"Фотографія: {photos[f'photo_item_{i}']}\n"
    report += "Проаналізуй фото і звіт та дай відповідь"
    print(report)
    return report


async def analyze_report(report, photo_urls) -> str | None:
    try:
        message_content = [{"type": "text", "text": report}]

        for url in photo_urls:
            message_content.append({
                "type": "image_url",
                "image_url": {"url": url}
            })

        response = client.chat.completions.create(
            model="gpt-4-vision-preview",
            messages=[{
                "role": "user",
                "content": message_content
            }],
            max_tokens=500
        )
        return response.choices[0].message.content if response.choices else None
    except Exception as e:
        print(f"Помилка під час аналізу звіту: {e}")
        return None

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
