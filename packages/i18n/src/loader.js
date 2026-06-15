// @danwa/i18n - i18n Loader
// English is the SSOT (Single Source of Truth)
// ~50 UI translations are modules in danwa-modules (loaded dynamically)
// 14 Core languages are deprecated

let currentLocale = 'en';
let translations = { en: {} };
let listeners = [];

const enFallback = {
  'common.loading': 'Loading...',
  'common.error': 'Error',
  'common.save': 'Save',
  'common.cancel': 'Cancel',
  'common.delete': 'Delete',
  'common.edit': 'Edit',
  'common.create': 'Create',
  'common.search': 'Search',
  'common.filter': 'Filter',
  'common.refresh': 'Refresh',
  'common.settings': 'Settings',
  'common.profile': 'Profile',
  'common.logout': 'Logout',
  'common.login': 'Login',
  'nav.dashboard': 'Dashboard',
  'nav.blueprints': 'Blueprints',
  'nav.workflow_templates': 'Workflow Templates',
  'nav.agents': 'Agents',
  'nav.prompts': 'Prompts',
  'nav.roles': 'Roles',
  'nav.tones': 'Tone Profiles',
  'nav.llm': 'LLM Profiles',
  'nav.modules': 'Modules',
  'nav.input_composer': 'Input Composer',
  'nav.output_composer': 'Output Composer',
  'nav.exec': 'Workflow Exec',
  'nav.diff': 'Diff View',
  'nav.replay': 'Replay View',
  'nav.proposals': 'Proposals',
  'nav.translations': 'Translations',
  'nav.tenants': 'Tenants',
  'nav.users': 'Users',
  'nav.health': 'Server Health',
  'nav.system': 'System Management',
  'nav.profile': 'My Profile',
  'nav.my_keys': 'My API Keys',
};

async function loadLocale(locale) {
  if (locale === 'en') {
    translations.en = { ...enFallback, ...translations.en };
    return translations.en;
  }

  try {
    const module = await import(`../../../../danwa-modules/i18n-${locale}/ui_strings.json`);
    translations[locale] = { ...enFallback, ...module.default };
  } catch (e) {
    console.warn(`Language module i18n-${locale} not found, falling back to English`);
    translations[locale] = { ...enFallback };
  }

  return translations[locale];
}

export const i18n = {
  init: async () => {
    const saved = localStorage.getItem('locale');
    const browser = navigator.language.split('-')[0];
    currentLocale = saved || browser || 'en';
    await loadLocale(currentLocale);
    i18n.notify();
  },

  getLocale: () => currentLocale,

  setLocale: async (locale) => {
    currentLocale = locale;
    localStorage.setItem('locale', locale);
    await loadLocale(locale);
    i18n.notify();
  },

  t: (key, params = {}) => {
    const localeTranslations = translations[currentLocale] || translations.en || enFallback;
    let text = localeTranslations[key] || translations.en?.[key] || enFallback[key] || key;

    Object.entries(params).forEach(([k, v]) => {
      text = text.replace(new RegExp(`\\{${k}\\}`, 'g'), v);
    });

    return text;
  },

  subscribe: (callback) => {
    listeners.push(callback);
    return () => {
      listeners = listeners.filter(cb => cb !== callback);
    };
  },

  notify: () => {
    listeners.forEach(cb => cb(currentLocale, translations[currentLocale]));
  },

  getTranslations: () => translations[currentLocale] || {},
};