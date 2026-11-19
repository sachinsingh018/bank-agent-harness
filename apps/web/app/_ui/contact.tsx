// The contact call to action on the landing and about pages: an email link with the subject
// filled in, and a LinkedIn link. Plain links, no form, so nothing is stored or sent by the site.

import styles from "./contact.module.css";

const EMAIL = "mailto:sachinsingh018@gmail.com?subject=Bank%20agent%20harness%20demo%20request";
const LINKEDIN = "https://www.linkedin.com/in/sac108";

export function Contact() {
  return (
    <section className={styles.contact} aria-labelledby="contact-title">
      <h2 id="contact-title" className={styles.title}>
        Want to try it with your own data?
      </h2>
      <p className={styles.text}>
        Tell me which alert types or case work your team handles, and I will walk you through
        how the harness could run on them.
      </p>
      <p className={styles.actions}>
        <a href={EMAIL} className="button-link primary">
          Email me
        </a>
        <a href={LINKEDIN} className="button-link" rel="noopener noreferrer">
          Message me on LinkedIn
        </a>
      </p>
    </section>
  );
}
