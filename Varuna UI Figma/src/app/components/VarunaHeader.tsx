import React from 'react';
import { Bell, Search, Globe, Moon, Sun } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface VarunaHeaderProps {
  onThemeToggle: () => void;
  isDarkMode: boolean;
}

export const VarunaHeader: React.FC<VarunaHeaderProps> = ({ onThemeToggle, isDarkMode }) => {
  const { t, i18n } = useTranslation();

  const toggleLanguage = () => {
    const nextLng = i18n.language === 'en' ? 'pt' : 'en';
    i18n.changeLanguage(nextLng);
  };

  return (
    <header className="h-14 bg-white dark:bg-slate-900 border-b border-slate-100 dark:border-slate-800 px-4 flex items-center justify-between sticky top-0 z-50 transition-colors duration-300">
      <div className="flex items-center gap-4">
        {/* Placeholder for spacing */}
        <div className="w-6" />
      </div>

      <div className="flex-1 max-w-md mx-8 hidden md:block">
        <div className="relative group">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 group-focus-within:text-emerald-500 transition-colors" />
          <input 
            type="text" 
            placeholder={t('Search')}
            className="w-full bg-slate-50 dark:bg-slate-800 border-none rounded-xl py-2 pl-10 pr-4 text-sm focus:ring-2 focus:ring-emerald-500/20 dark:text-white transition-all outline-none"
          />
        </div>
      </div>

      <div className="flex items-center gap-2">
        {/* Language Toggle */}
        <button 
          onClick={toggleLanguage}
          className="flex items-center gap-2 px-3 py-1.5 text-xs font-bold text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800 rounded-lg transition-all border border-slate-100 dark:border-slate-800 uppercase"
        >
          <Globe className="w-3.5 h-3.5" />
          {i18n.language}
        </button>

        {/* Theme Toggle */}
        <button 
          onClick={onThemeToggle}
          className="p-2 text-slate-400 hover:text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/20 rounded-lg transition-all"
        >
          {isDarkMode ? <Sun className="w-4.5 h-4.5" /> : <Moon className="w-4.5 h-4.5" />}
        </button>
        
        <button className="p-2 text-slate-400 hover:text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/20 rounded-lg transition-all relative">
          <Bell className="w-4.5 h-4.5" />
          <span className="absolute top-2 right-2 w-1.5 h-1.5 bg-rose-500 rounded-full border-2 border-white dark:border-slate-900"></span>
        </button>
        
        <div className="h-6 w-px bg-slate-100 dark:bg-slate-800 mx-2"></div>
        
        {/* User Profile Placeholder */}
        <div className="w-8 h-8 rounded-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center text-[10px] font-black text-slate-400">
          JD
        </div>
      </div>
    </header>
  );
};
