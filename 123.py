import json

def check_cities_file(path="cities.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ Ошибка при чтении файла: {e}")
        return

    if not isinstance(data, list):
        print("❌ Ожидался список городов, но получен другой тип:", type(data))
        return

    issues = []
    seen = set()
    for i, city in enumerate(data):
        if not isinstance(city, str):
            issues.append(f"[{i}] ❌ Не строка: {city}")
            continue

        original = city
        normalized = city.strip()

        if original != normalized:
            issues.append(f"[{i}] ⚠️ Лишние пробелы: '{original}' → '{normalized}'")

        if normalized == "":
            issues.append(f"[{i}] ⚠️ Пустая строка")

        lower = normalized.lower()
        if lower in seen:
            issues.append(f"[{i}] ⚠️ Дубликат: '{normalized}'")
        else:
            seen.add(lower)

    if issues:
        print("🔍 Найдены проблемы:")
        for issue in issues:
            print(issue)
    else:
        print("✅ Всё в порядке: файл содержит корректный список городов.")

# Запуск
check_cities_file()
