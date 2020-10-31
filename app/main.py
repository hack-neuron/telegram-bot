import asyncio
import logging
import os

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ParseMode
from aiogram.utils import executor

logging.basicConfig(level=logging.INFO)

API_TOKEN = os.environ['API_TOKEN']
NEURAMARK_TOKEN = os.environ['NEURAMARK_TOKEN']
BACKEND_API_URL = os.environ['BACKEND_API_URL']


bot = Bot(token=API_TOKEN)

# используем обычный MemoryStorage для хранения стейтов
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)


# стейты
class Form(StatesGroup):
    doc_markup = State()
    ai_markup = State()
    scan = State()


async def get_file(message):
    file_id = message.document.file_id
    return await bot.get_file(file_id)


@dp.message_handler(commands='start')
async def cmd_start(message: types.Message):
    await Form.doc_markup.set()

    reply = (
        'Привет! Я могу оценить разметку ИИ-сервиса!',
        'Отправь мне разметку эксперта документом. Жду ⏳'
    )
    await message.reply('\n'.join(reply))


@dp.message_handler(state='*', commands='cancel')
@dp.message_handler(Text(equals='Отмена', ignore_case=True), state='*')
async def cancel_handler(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return

    await state.finish()
    await message.reply('Отмена. Давай начнём заново?',
                        reply_markup=types.ReplyKeyboardRemove())


@dp.message_handler(content_types=types.ContentType.DOCUMENT,
                    state=Form.doc_markup)
async def process_doc_markup(message: types.Document, state: FSMContext):
    async with state.proxy() as data:
        data['doc_markup'] = await get_file(message)

    await Form.next()
    await message.reply('Отлично! Теперь отправь разметку ИИ-сервиса!')


@dp.message_handler(content_types=types.ContentType.DOCUMENT,
                    state=Form.ai_markup)
async def process_ai_markup(message: types.Document, state: FSMContext):
    await state.update_data(ai_markup=await get_file(message))

    await Form.next()
    await message.reply('Замечательно! Теперь отправь рентгенограмму!')


@dp.message_handler(content_types=types.ContentType.DOCUMENT,
                    state=Form.scan)
async def process_scan(message: types.Message, state: FSMContext):
    await state.update_data(scan=await get_file(message))

    async with state.proxy() as data:
        form = aiohttp.FormData()
        for k, v in data.items():
            data = await bot.download_file(v.file_path)
            form.add_field(k, data,
                           content_type='image/png',
                           filename=v['file_unique_id'])

        async with aiohttp.ClientSession() as client:
            async with client.post(BACKEND_API_URL + '/upload',
                                   params={'token': NEURAMARK_TOKEN},
                                   data=form) as resp:
                result = await resp.json()

    try:
        task_id = result['id']
    except KeyError:
        await state.finish()
        await message.reply('Что-то пошло не так :( Попробуйте ещё раз!')
        return

    await bot.send_message(message.chat.id,
                           'Идёт обработка. Пожалуйста подождите!')

    while True:
        async with aiohttp.ClientSession() as client:
            async with client.get(BACKEND_API_URL + '/get_status',
                                  params={
                                      'token': NEURAMARK_TOKEN,
                                      'id_': task_id
                                    }) as resp:
                status = await resp.json()

        if status['state'] == 'SUCCESS':
            await state.finish()
            rating = round(status['result']['rating'] * 100)
            await bot.send_message(
                message.chat.id,
                f'Качество разметки ИИ-сервиса: `{rating}%`',
                parse_mode=ParseMode.MARKDOWN)
            break

        if status['state'] == 'FAILURE':
            await state.finish()
            await message.reply('Что-то пошло не так :( Попробуйте ещё раз!')
            break

        await asyncio.sleep(1)


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
