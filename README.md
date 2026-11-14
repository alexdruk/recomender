# Book recommender

A personal, fully local book recommender for the Flibusta daily updates.

## What it does

1. **Downloads** the daily update files (`f.fb2.*.zip`) from `https://flibusta.is/daily/`
   (fallback `https://flibusta.site/daily/`), one at a time, with a pause of at
   least 3 seconds between requests. Each zip is parsed and then deleted.
2. **Filters** the new books: Russian language, fiction, not a collection, at
   least 10,000 words, not already in my library, and the series rule
   (standalone, or book 1, or a series where I already liked an earlier book).
3. **Fingerprints** every book that passed, from its text only: stylometry,
   embeddings of the whole text and of the opening (first 25,000 words, model
   bge-m3), genre codes from the FB2 file, length, translated or not.
   Ratings, votes, comments and the year are never used.
4. **Scores** the book with a model trained on my liked books against a
   background of ordinary books, and stores the score and its percentile among
   all feed books scored so far.
5. **Shows** the best books on a local page. I pick books for the waiting
   list, send them to iBooks, and give **Like** or **Dislike** after reading.
   The model retrains after every 10 answers and rescores the books that are
   still waiting. A Dislike on a card I never read counts 3 times, a Dislike
   after reading counts 5 times.

## Commands

| Command | What it does |
| --- | --- |
| `npm start` | Starts the page at http://localhost:3000 and prints the address for the iPad. Runs the daily loop at start and then every 6 hours. |
| `npm run check-page` | Checks that `src/page.html` keeps the Safari 9 rules (ES5 script, no CSS grid). |
| `npm run daily` | Runs the daily loop once: download, parse, filter, fingerprint, score. |
| `npm run train` | Trains the model, saves it to `data/model/recommender.joblib`, prints the quality, rescores waiting books. |
| `npm run autostart-on` | Starts the server at every login and keeps it running (macOS launchd). Log: `data/server.log`. |
| `npm run autostart-off` | Stops the server and removes the autostart job. |

## The page

- **Recommended**: unanswered books in the top 5% (the share can be changed on
  the page), best first. Each card shows author, title, series, genres, length,
  annotation, score, the 3 liked books closest by text, and a link to the book.
- **Show the rest**: the other books of the last 7 days, ranked.
- **Status line**: last download, books analysed, books that passed the
  filters, answers given, model quality from the last training.
- Each card has **Add to send list**, **Like** and **Dislike**. Sending a book
  is never a verdict: "Add to send list" only moves the book to the waiting
  list. **Add all to send list** moves every recommended book there.
- **To send** (`/tosend`): the waiting books in the order I added them. When
  the page opens, the server gets their EPUB files in the background, one at
  a time, and shows "ready" or "preparing". **Read in iBooks** sends the file
  (Safari on the iPad offers "Open in iBooks") and moves the book to
  "Reading", so the next waiting book is at the top. **Remove** puts a book
  back in the recommendations.
- **Reading** (`/reading`): the sent books, newest first, with **Like**,
  **Dislike** and **Send again**. An answer given here is stored as "after
  reading": the Like makes the book one of my liked books, the Dislike is a
  strong negative (weight 5).
- **Liked** and **Disliked** (`/liked`, `/disliked`): my answers, newest
  first. Each can be changed or cleared; a cleared book goes back to where it
  was ("Reading" or the recommendations).
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

## Settings (`config.json`)

`filters` (each filter can be switched off or changed), `topPercent`,
`restDays`, `retrainEveryAnswers`, `checkEveryHours`, `port`, `host`, `mirrors`,
`pauseSeconds`.

## Where things are

- `src/daily.js` the daily loop, `src/fetch-feed.js` the polite downloader,
  `src/parse-fb2.js` the FB2 parser, `src/stylometry.js` the style numbers.
- `src/server.js` and `src/page.html` the page, `src/check-page.js` the Safari 9 check.
- `src/net.js` the polite queue: one request at a time for all programs together
  (lock file `data/net.lock`), at least 3 seconds between requests.
- `model/recommend.py` training and scoring, `model/embed.py` embeddings.
- `data/` (not in git): the database `books.db`, texts, vectors, the model, the log.

Needs Node 22 (`npm install`) and Python in `.venv`
(`.venv/bin/pip install -r model/requirements.txt`). The older scripts and the
reports in `reports/` are the research that led to this model.
