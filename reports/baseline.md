# Baseline score

Built by `model/baseline.py` from `data/books.db`. The score answers one question: *does this book look more like the books I liked than like an average new book in the feed?* It only looks at simple facts about a book (genre, length, and so on), not at the text.

## Books used

| Group | Books |
| --- | --- |
| Liked books in the archive | 309 |
| ... left out as collections | 11 |
| **Liked books used (positive class)** | **298** |
| Feed books collected | 369 |
| ... left out: not Russian-language | 7 |
| ... left out: not fiction | 45 |
| ... left out: already in the archive | 0 |
| ... left out as collections | 3 |
| **Feed books used (unlabeled class)** | **314** |
| Different author groups (co-authors count as one group) | 425 |

Liked books that still have no genre: 0 of 309.

"Unlabeled" does not mean "disliked". These are simply new books nobody has judged yet; some of them would be liked. So the model can never look perfect, and that is fine.

Genres, year, series and "translated" come from the Flibusta catalog for both liked and feed books, so both groups are described by the same source. The codes written inside a feed file are used only when the catalog has nothing for that book.

## Genre groups: liked vs unlabeled

A book can be in several groups, so the columns add up to more than 100%.

| Genre group | Liked books | Liked share | Unlabeled books | Unlabeled share | Difference (points) |
| --- | --- | --- | --- | --- | --- |
| romantic fantasy | 124 | 41.6% | 74 | 23.6% | +18.0 |
| web literature | 118 | 39.6% | 90 | 28.7% | +10.9 |
| detective | 40 | 13.4% | 29 | 9.2% | +4.2 |
| fantasy | 79 | 26.5% | 72 | 22.9% | +3.6 |
| humour | 23 | 7.7% | 15 | 4.8% | +2.9 |
| contemporary prose | 23 | 7.7% | 16 | 5.1% | +2.6 |
| nonfiction | 4 | 1.3% | 0 | 0.0% | +1.3 |
| science fiction | 46 | 15.4% | 47 | 15.0% | +0.5 |
| historical | 8 | 2.7% | 7 | 2.2% | +0.5 |
| war prose | 3 | 1.0% | 2 | 0.6% | +0.4 |
| boyar-anime | 5 | 1.7% | 6 | 1.9% | -0.2 |
| adventure | 9 | 3.0% | 11 | 3.5% | -0.5 |
| poetry and drama | 0 | 0.0% | 2 | 0.6% | -0.6 |
| children | 0 | 0.0% | 4 | 1.3% | -1.3 |
| other | 1 | 0.3% | 7 | 2.2% | -1.9 |
| classic prose | 2 | 0.7% | 9 | 2.9% | -2.2 |
| thriller | 8 | 2.7% | 17 | 5.4% | -2.7 |
| horror and mystic | 3 | 1.0% | 12 | 3.8% | -2.8 |
| fan translation | 8 | 2.7% | 21 | 6.7% | -4.0 |
| litrpg | 5 | 1.7% | 19 | 6.1% | -4.4 |
| alternative history | 8 | 2.7% | 26 | 8.3% | -5.6 |
| erotica | 4 | 1.3% | 23 | 7.3% | -6.0 |
| contemporary romance | 8 | 2.7% | 62 | 19.7% | -17.1 |
| popadantsy | 34 | 11.4% | 93 | 29.6% | -18.2 |

Books with no genre group at all: 0 liked, 0 unlabeled.

### Unmapped genre codes

These codes are not in `data/genre-map.json` and are ignored by the model. "Books" counts only books whose genres really came from that code.

| Code | Books |
| --- | --- |
| `collection` | 0 |
| `compilation` | 0 |
| `praktika` | 0 |
| `sketch` | 0 |

## How well the models rank

Three logistic regression models:

- **a** - genre groups only
- **b** - genre groups + length, translated or not, part of a series or not, year
- **c** - model b + "known author" (the author has another liked book)

Each model is tested with 5-fold cross-validation, repeated 10 times with different shuffles. Two kinds of split:

- **random** - books are dealt into folds at random. An author can be on both sides, so this flatters the model.
- **author-grouped** - all books of an author (and co-authors) stay together on one side. This is the honest test for finding *new* authors.

The "known author" feature is computed from the training part only, and a book never counts as its own evidence.

| Model | Split | ROC AUC | Liked test books in top 10% |
| --- | --- | --- | --- |
| a: genres | random | 0.777 ± 0.033 | 16.9% ± 1.9 |
| b: + length, translated, series, year | random | 0.809 ± 0.030 | 17.1% ± 1.9 |
| c: + known author | random | 0.881 ± 0.030 | 19.9% ± 0.6 |
| a: genres | author-grouped | 0.759 ± 0.053 | 16.4% ± 2.5 |
| b: + length, translated, series, year | author-grouped | 0.791 ± 0.050 | 16.7% ± 2.3 |
| c: + known author | author-grouped | 0.779 ± 0.058 | 15.8% ± 2.8 |

How to read the numbers:

- **ROC AUC**: 0.5 is coin-flipping, 1.0 is a perfect ranking. The number after ± shows how much it moves from fold to fold.
- **Top 10%**: the test books are sorted by score and the top tenth is taken. The figure is the share of all liked test books found there. Guessing gives 10.0%. Because about 48.7% of the test books are liked, even a perfect ranking can reach only about 20.1%.

- **Known author**: in the random split it moves AUC from 0.809 to 0.881. In the author-grouped split it cannot help (from 0.791 to 0.779), because no test author is ever seen in training. So this feature is about following authors you already read, not about discovering new ones.

### A caution about "year"

The liked books were collected over many years (median year 2022), while the unlabeled books are this week's uploads (median year 2026). So "year" partly tells the model *which pile a book came from*, not *whether it is good* (its weight in model b is -1.70). As a check, here is model b with the year taken out:

| Model | Split | ROC AUC | Liked test books in top 10% |
| --- | --- | --- | --- |
| a: genres | random | 0.777 ± 0.033 | 16.9% ± 1.9 |
| b without year | random | 0.802 ± 0.030 | 17.5% ± 1.7 |
| b: with year | random | 0.809 ± 0.030 | 17.1% ± 1.9 |
| a: genres | author-grouped | 0.759 ± 0.053 | 16.4% ± 2.5 |
| b without year | author-grouped | 0.787 ± 0.052 | 16.7% ± 2.4 |
| b: with year | author-grouped | 0.791 ± 0.050 | 16.7% ± 2.3 |

In the author-grouped test the year changes AUC by +0.004, which is far less than the fold-to-fold noise. The step from model a to model b (+0.032) therefore comes from length, translation and series, not from the year. Keep an eye on this as the feed collection grows.

## Which genre groups push the score

Weights of model a (genre groups only), trained on all books. A positive weight pushes a book towards "liked", a negative one away. Groups with very few books have unreliable weights. "nonfiction" is high only because non-fiction was filtered out of the unlabeled set but not out of the liked books; ignore it.

Pushing the score up most:

| Genre group | Weight | Liked books | Unlabeled books |
| --- | --- | --- | --- |
| nonfiction | +0.85 | 4 | 0 |
| web literature | +0.85 | 118 | 90 |
| humour | +0.20 | 23 | 15 |
| romantic fantasy | +0.18 | 124 | 74 |
| historical | +0.16 | 8 | 7 |
| boyar-anime | +0.12 | 5 | 6 |
| contemporary prose | +0.11 | 23 | 16 |

Pushing the score down most:

| Genre group | Weight | Liked books | Unlabeled books |
| --- | --- | --- | --- |
| contemporary romance | -2.32 | 8 | 62 |
| popadantsy | -1.66 | 34 | 93 |
| classic prose | -1.44 | 2 | 9 |
| erotica | -1.42 | 4 | 23 |
| children | -1.25 | 0 | 4 |
| fan translation | -1.23 | 8 | 21 |
| horror and mystic | -1.22 | 3 | 12 |
| litrpg | -1.16 | 5 | 19 |

Other features in model c, trained on all books (numbers are scaled, so sizes can be compared roughly):

| Feature | Weight |
| --- | --- |
| length (log of words) | +0.62 |
| translated | +0.12 |
| part of a series | +0.01 |
| year | -1.48 |
| year unknown | +0.22 |
| known author | +3.04 |

## The 20 unlabeled books with the highest score

Scores here and in the next table come from model b in the author-grouped test: every book is scored by models that never saw the book or its author. The score is between 0 and 1; higher means more like the liked books. "Known author" marks authors who already have a liked book.

| Score | Key | Author | Title | Genres | Known author |
| --- | --- | --- | --- | --- | --- |
| 0.95 | 895610 | Жан Грива | Том 2. Рассказы | prose_military, story |  |
| 0.93 | 895611 | Жан Грива | Том 1. Любовь и ненависть | prose_military, great_story |  |
| 0.90 | 895376 | Юлия Григорова | Танец драконов и ключей. Том 2 | network_literature, love_sf |  |
| 0.89 | 895580 | Айза Таллер | Обсидиановый огонь | love_sf, network_literature, sf_heroic |  |
| 0.89 | 895375 | Юлия Григорова | Танец драконов и ключей. Том 1 | network_literature, love_sf |  |
| 0.87 | 895256 | Мария Камардина | Тёмное сердце Белого озера. Книга 1 | network_literature, love_sf |  |
| 0.87 | 895427 | Маргарет Лэндон | Анна и король Сиама | prose_classic |  |
| 0.86 | 895453 | Долорес Медио | Летний фарс | prose_contemporary |  |
| 0.84 | 895258 | Ирина Осинская | Тайна семицветных осколков | love_sf, fantasy_det, network_literature |  |
| 0.84 | 895543 | Джордж Макдональд Фрейзер | Флэшмен и Ангел Господень | adv_history |  |
| 0.83 | 895577 | Фолькер Браун, Ангела Стахова, Вальтрауд Арндт, Хельга Кёнигсдорф, Вольфганг де Бройн, Кристоф Хайн, Мартин Штефан, Хельга Шуберт, Эрих-Гюнтер Зассе, Эльфрида Брюнинг, Вольфганг Трампе, Вернер Ленц, Хорст Маттис, Юрий Кох | Неоконченная история | prose_contemporary |  |
| 0.82 | 895333 | Екатерина Богданова | Ведьмины приключения, или Как Сита охотилась на директора | network_literature, love_sf, magic_school |  |
| 0.81 | 895549 | Велесогор Волх | Чтец Горизонтов | network_literature, slavic_fantasy, sf_action, sf_heroic |  |
| 0.81 | 895634 | Квинтус Номен | Капитан ближнего плавания | network_literature, sf_social, sf_space |  |
| 0.80 | 895504 | Татьяна Лаас | Три дня счастья | love_sf, network_literature, sf_detective |  |
| 0.79 | 895406 | Кира Уайт | Точка. Трилогия | love_sf, love_detective, sf_heroic |  |
| 0.78 | 895305 | Рина Чар | Истинным злодеям свет (не) к лицу | sf_fantasy, love_sf, detective, network_literature |  |
| 0.77 | 895502 | Инди Видум | Осторожно, магия открывается! | network_literature, sf_fantasy_city, everyday_fantasy | yes |
| 0.76 | 895387 | Алексей Свадковский | Стальной рыцарь 3 | sf_stimpank, sf_action, network_literature |  |
| 0.76 | 895281 | Елена Свободная | Белая целительница: вас много, а я одна! | love_sf, sf_humor |  |

## The 20 liked books with the lowest score

These are liked books the model would have missed. They show what the simple features cannot see.

| Score | Key | Author | Title | Genres |
| --- | --- | --- | --- | --- |
| 0.00 | 881239 | Марк Коултер | Никогда не ошибается | sf_fantasy, fan_translation |
| 0.07 | 50164 | Роберт Шекли | Запах мысли | sf |
| 0.12 | 698028 | Илья Александрович Шумей | Старенький Принц | sf_humor, sf_space |
| 0.13 | 708724 | Роман Прокофьев | Звездная Кровь | network_literature, sf_technofantasy, sf_realrpg, popadancy |
| 0.13 | 886970 | Кирилл Коваль | Шеф-повар придорожной таверны IV | other, popadancy |
| 0.14 | 328899 | Василий М. Маханенко | Барлиона | sf_litrpg, popadancy |
| 0.15 | 834556 | Инди Видум | Договор | boyar_anime, network_literature, sf_realrpg, popadancy |
| 0.17 | 276697 | Элеонора Раткевич | Повелитель | sf_fantasy |
| 0.17 | 806624 | Наталья Филимонова | Сорок дракошек | network_literature, everyday_fantasy, popadancy, sf_humor |
| 0.17 | 58907 | Михаил Веллер | Памятник Дантесу | prose_contemporary |
| 0.18 | 803392 | Наталья Сергеевна Филимонова | Это вам не сорок кошек! (СИ) | everyday_fantasy, sf_humor, popadancy, network_literature |
| 0.19 | 682658 | Тори Ру | Время неба | love_contemporary |
| 0.20 | 861032 | Ольга Алексеевна Ярошинская | Бесценная | love_sf, network_literature, love_short |
| 0.20 | 866144 | Сева Сотх | Ретро Бит [2] | popadancy, network_literature, sf_history |
| 0.20 | 888451 | Сева Сотх | Ретро Бит [8] | popadancy, network_literature, sf_history |
| 0.20 | 863309 | Сева Сотх | Ретро Бит | popadancy, network_literature, sf_history |
| 0.21 | 877891 | Сева Сотх | Ретро бит [5] | popadancy, network_literature, sf_history |
| 0.22 | 449763 | Влас Михайлович Дорошевич | Дело о людоедстве | prose_rus_classic, story |
| 0.23 | 849511 | Аня Истомина | Как приручить полковника | love_contemporary |
| 0.23 | 160102 | Наталья Нестерова | Девушка с приветом | love_contemporary |
