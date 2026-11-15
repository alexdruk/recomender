# Book recommender

A personal, fully local book recommender for the Flibusta daily updates.

## What it does

1. **Downloads** the daily update files (`f.fb2.*.zip`) from `https://flibusta.is/daily/`
   (fallback `https://flibusta.site/daily/`), one at a time, with a pause of at
   least 3 seconds between requests. Each zip is parsed and then deleted.
2. **Refreshes the reader ratings** at most once a day: the ratings dump of the
   library and two small dumps (which book belongs to which author and series),
   through the same polite queue. The files are kept in `data/sql/`.
3. **Filters** the new books: Russian language, fiction, not a collection, at
   least 10,000 words, not already in my library, and the series rule
   (standalone, or book 1, or a series where I scored an earlier book 4 or 5).
   The fiction rule is strict: a book is rejected if **any** of its genre codes
   is non-fiction or unknown, and it needs at least one fiction code. Then a
   **text check** (a small fiction-against-non-fiction classifier on the text
   embedding) sets aside books whose codes say fiction but whose text does not.
4. **Fingerprints** every book that passed: stylometry, embeddings of the whole
   text and of the opening (first 25,000 words, model bge-m3), genre codes from
   the FB2 file, length, translated or not, plus the reader ratings:
   - the book's own rating, shrunk: `(sum of marks + average x 5) / (votes + 5)`,
     where "average" is the real catalog average for fiction (about 3.4). A book
     without votes sits exactly at that average: **no votes is never bad**;
   - the number of votes (log);
   - the author's track record: the shrunk rating over all **earlier** books of
     the same author, and how many of them have marks. The book itself is never
     part of it. Neutral when the author is new;
   - the same track record for the series, when the book continues one.
5. **Scores** the book with a model that predicts **my score from 1 to 5**, and
   stores the predicted score and its percentile among all feed books. Every
   unanswered book of the last 60 days is scored again in each loop, so a book
   moves when votes arrive. A book that rises into the recommended range after
   new votes gets the note "now rated". A book with at least 5 votes and an
   average below 3 is hidden.
6. **Shows** the best books on a local page. I pick books for the waiting
   list, send them to iBooks, and give each a **score from 1 to 5** (1 = bad,
   3 = average, 5 = excellent), from the card or after reading. The model
   retrains after every 10 new or changed scores.

## The model

- Target: my kept library books count as 5; a scored book counts as its score
  (weight 5 when scored after reading, weight 2 when scored from the card
  only); background books count as 2 with the low weight 0.3; my old "would
  never read" answers count as 1.
- In training the book's own rating is hidden for a random half of the books,
  so the model learns to score both rated and unrated books.
- Ridge regression and gradient boosting are both tried; the one that ranks
  better in an author-grouped test is kept.
- From 30 scores on, the status line shows how well the ranking agrees with my
  scores on books the model had not seen (Spearman correlation), and my average
  score for its top 10.
- `reports/model.html` (menu: **Model report**) is rebuilt after every
  training: the training data, the factors used and not used, the ranking
  quality of each group of factors (for rated and for unrated books), the
  distribution of model scores, the strongest factors, and my scores against
  the predictions.

## The reading model

A local language model reads every interesting book and rates it. Nothing
leaves this computer.

Setup (once):

```
brew install ollama
brew services start ollama
ollama pull qwen3.5:9b
```

- The model is `qwen3.5:9b` (about 10 GB in memory; together with the
  embedding model it stays under 14 GB). It was picked from three candidates
  by a stability test: it gives the same book almost the same ratings when it
  reads other passages of it, and it uses the whole 1 to 5 scale.
- It reads three passages of 2,500 words: from the start, the middle and the
  end of the first 100 pages (25,000 words). That takes about 70 seconds a
  book. Reading all 25,000 words takes 3 minutes a book, too slow for the
  first run over 600 books.
- For each passage it answers with checked JSON (a wrong answer is asked
  again, up to 3 times, temperature 0): 13 qualities rated 1 to 5 (plot pace,
  plot originality, depth of characters, psychological complexity, quality of
  dialogue, humour and irony, emotional intensity, darkness and violence,
  weight of the romance line, weight of explicit content, world-building,
  richness of language, literary polish), the narrative voice (first or third
  person, tone), up to 5 themes from a fixed list, and a short summary. The
  book gets the average of its passages and a two-sentence summary.
- The ratings are a new group of factors in the scoring model. There are now
  two models: the **cheap** one (everything except the reading) scores every
  new book; the **full** one (with the reading) gives the final score of the
  books that were read. Every training compares the two in the author-grouped
  test, with shuffled ratings as a noise control, and the model report says
  plainly whether the reading beats the noise.
- Daily loop: the cheap model scores every new book, the reading model reads
  the best 30 of the day, and their final score uses its ratings. Books that
  were not read keep the cheap score and carry the mark "not read by the
  model".
- A card of a read book shows the two-sentence summary, the themes, and the
  three qualities that most raised and most lowered its score.
- `reports/portrait.md` is the portrait of my taste: my library against
  ordinary new books, quality by quality and theme by theme. The same
  comparison is a chart in `reports/model.html`.

## Commands

| Command | What it does |
| --- | --- |
| `npm start` | Starts the page at http://localhost:3000 and prints the address for the iPad. Runs the daily loop at start and then every 6 hours. |
| `npm run check-page` | Checks that `src/page.html` keeps the Safari 9 rules (ES5 script, no CSS grid). |
| `npm run daily` | Runs the daily loop once: download, ratings, filter, fingerprint, score. |
| `npm run ratings` | Refreshes the reader ratings (at most once a day) and looks up every book. `node src/ratings.js --force` downloads now. |
| `npm run cleanup` | Runs the automatic cleaning once (it also runs at the end of every daily loop). `node src/cleanup.js --dry-run` only says what would be deleted. |
| `npm run train` | Trains the text check and the model, saves the model to `data/model/recommender.joblib`, prints the quality, rescores the feed books, rebuilds `reports/model.html`. |
| `npm run read-all` | The long run of the reading model: all library books, all scored books and random background books, as many as fit in 12 hours. It can be stopped and started again; it trains the model at the end. Log: `data/logs/reader.log`. |
| `npm run read-status` | One line: how far the long run is. |
| `npm run read-check` | Stability test: reads 20 books twice and prints the agreement for each quality. |
| `npm run portrait` | Rebuilds `reports/portrait.md`. |
| `npm run autostart-on` | Starts the server at every login and keeps it running (macOS launchd). Log: `data/server.log`. |
| `npm run autostart-off` | Stops the server and removes the autostart job. |

## The page

- **Recommended**: unanswered books in the top 5% (the share can be changed on
  the page), best first. Each card shows author, title, series, genres, length,
  the predicted score, "rating 4.3 from 12 votes" or "no votes yet", the
  author's track record in the same form (and the series record), the
  annotation, the 3 liked books closest by text, and a link to the book.
- **Show the rest**: the other books of the last 7 days, ranked.
- **Status line**: last download, when the ratings were refreshed, books
  analysed, books that passed the filters, books hidden for a low rating,
  scores given, and the model quality from the last training.
- Each card has **Add to send list** and five score buttons, **1 to 5**.
  Sending a book is never a verdict: "Add to send list" only moves the book to
  the waiting list. **Add all to send list** moves every recommended book there.
- **To send** (`/tosend`): the waiting books in the order I added them. When
  the page opens, the server gets their EPUB files in the background, one at
  a time, and shows "ready" or "preparing". **Read in iBooks** sends the file
  (Safari on the iPad offers "Open in iBooks") and moves the book to
  "Reading", so the next waiting book is at the top. **Remove** puts a book
  back in the recommendations.
- **Reading** (`/reading`): the sent books, newest first, with the five score
  buttons and **Send again**. A score given here is stored as "after reading"
  and counts more (weight 5) than a score from the card only (weight 2).
- **My scores** (`/scores`): every book I scored or was sent, newest first,
  with its current score and the five buttons, so any score can be changed at
  any time. Old Likes start as 4 and old Dislikes as 2, marked "not rescored"
  until I press a number. **Clear score** puts a book back where it was
  ("Reading" or the recommendations). There is no queue of books I have not seen.
- **Model report** (`/report`): opens `reports/model.html`.
- The menu shows the number of books beside each list name.
- Books that wait in "To send" or "Reading" are left out of training until
  they get an answer.
- **Check now**: runs the daily loop right away.
- A yellow warning appears if update files left the listing before they were
  downloaded (the computer was off for too long).

## iPad

The server listens on the home network. At start it prints the addresses to
type in Safari on the iPad (same Wi-Fi), for example
`http://alexs-laptop.local:3000` and `http://192.168.1.14:3000`. The IP
address can change; the `.local` name stays. To close the page to other
devices set `"host": "127.0.0.1"` in `config.json`.

The page runs in Safari 9 (iOS 9.3): the script is ES5 only and the style
uses no CSS grid, variables or `gap`. Run `npm run check-page` after every
change of `src/page.html`.

`/epub/<id>` gets the mirror's own EPUB download (`<mirror>/b/<id>/epub`,
second mirror if the first fails) through the polite queue, keeps it in
`data/epub/`, moves the book to "Reading" (no verdict is recorded), and sends
the file as `application/epub+zip` named `Author - Title.epub` in Latin letters.

## Automatic cleaning

At the end of every daily loop `src/cleanup.js` deletes:

- update zips that were already parsed;
- texts of feed books older than 14 days that are in no list. A text is kept
  while the book is on the recommendations page, in "To send", "Reading" or
  "My scores", or while its vectors are not saved yet;
- cached EPUB files older than 30 days, except books still waiting in
  "To send". A deleted EPUB is simply downloaded again if I ask for it.

Texts of liked books, vectors, the model, answers and the config are never
touched.

## Settings (`config.json`)

`filters` (each filter can be switched off or changed), `topPercent`,
`restDays`, `retrainEveryAnswers`, `ratingsEveryHours` (never less than 24),
`rescoreDays`, `checkEveryHours`, `port`, `host`, `mirrors`,
`pauseSeconds`, `cleanup` (`feedTextDays`, `epubDays`),
`reader` (`model`, `openingWords`, `partWords`, `readWords`, `contextTokens`,
`dailyBooks`). After a change of the reader model or of the passage sizes the
books must be read again (`npm run read-all`).

## Where things are

- `src/daily.js` the daily loop, `src/fetch-feed.js` the polite downloader,
  `src/parse-fb2.js` the FB2 parser, `src/stylometry.js` the style numbers.
- `src/server.js` and `src/page.html` the page, `src/check-page.js` the Safari 9 check.
- `src/net.js` the polite queue: one request at a time for all programs together
  (lock file `data/net.lock`), at least 3 seconds between requests.
- `src/ratings.js` the reader ratings: the daily download and the votes, author
  record and series record of every book (table `rating_facts`).
- `model/recommend.py` training and scoring, `model/fiction_check.py` the text
  check against non-fiction, `model/report.py` the model report,
  `model/embed.py` embeddings.
- `model/reader.py` the reading model (tables `reading_parts` and `readings`),
  `model/portrait.py` the portrait of my taste.
- `data/` (not in git): the database `books.db`, texts, vectors, the model, the log.

Needs Node 22 (`npm install`) and Python in `.venv`
(`.venv/bin/pip install -r model/requirements.txt`). The older scripts and the
reports in `reports/` are the research that led to this model.
