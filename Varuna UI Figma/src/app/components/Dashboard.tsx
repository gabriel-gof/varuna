import React, { useState, useEffect } from 'react';
import { 
  Users, 
  Wifi, 
  WifiOff, 
  AlertTriangle, 
  Zap, 
  HelpCircle,
  TrendingDown,
  Server,
  ChevronDown,
  Clock,
  RefreshCcw,
  Filter,
  Download,
  Search
} from 'lucide-react';
import { 
  LineChart, 
  Line, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer, 
  AreaChart, 
  Area 
} from 'recharts';
import { useTranslation } from 'react-i18next';
import { motion, AnimatePresence } from 'motion/react';
import * as Popover from '@radix-ui/react-popover';

// Mock data generator for specific OLTs
const generateOltTrendData = (seed: number) => [
  { name: '08:00', offline: 120 + seed, dyingGasp: 40 + seed, linkLoss: 70 + seed },
  { name: '10:00', offline: 140 + seed, dyingGasp: 45 + seed, linkLoss: 85 + seed },
  { name: '12:00', offline: 130 + seed, dyingGasp: 38 + seed, linkLoss: 90 + seed },
  { name: '14:00', offline: 210 + seed, dyingGasp: 60 + seed, linkLoss: 110 + seed },
  { name: '16:00', offline: 190 + seed, dyingGasp: 55 + seed, linkLoss: 105 + seed },
  { name: '18:00', offline: 170 + seed, dyingGasp: 50 + seed, linkLoss: 95 + seed },
  { name: '20:00', offline: 150 + seed, dyingGasp: 45 + seed, linkLoss: 88 + seed },
];

const StatCard = ({ title, value, icon: Icon, color, subtext }: { title: string, value: number, icon: any, color: string, subtext?: string }) => (
  <div className="bg-white dark:bg-slate-900 border border-slate-100 dark:border-slate-800 p-6 rounded-2xl shadow-sm hover:shadow-lg hover:-translate-y-1 transition-all duration-300 group cursor-default">
    <div className="flex items-center justify-between mb-4">
      <div className={`w-12 h-12 rounded-xl ${color} bg-opacity-10 flex items-center justify-center text-${color.split('-')[1]}-600 dark:text-${color.split('-')[1]}-400 group-hover:scale-110 transition-transform`}>
        <Icon className="w-6 h-6" />
      </div>
      {subtext && <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">{subtext}</span>}
    </div>
    <h4 className="text-[11px] font-black text-slate-400 uppercase tracking-widest mb-1">{title}</h4>
    <p className="text-3xl font-black text-slate-900 dark:text-white">{value.toLocaleString()}</p>
  </div>
);

const ExpandableOltRow = ({ name, authorized, online, offline, dyingGasp, linkLoss, unknown, seed }: any) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const data = generateOltTrendData(seed);

  return (
    <div className="flex flex-col gap-4 w-full">
      <div 
        onClick={() => setIsExpanded(!isExpanded)}
        className={`
          flex items-center px-6 py-5 bg-white dark:bg-slate-900 rounded-[28px] border-2 transition-all duration-300 cursor-pointer shadow-sm min-h-[88px]
          ${isExpanded ? 'border-emerald-500 ring-4 ring-emerald-500/5 shadow-md' : 'border-emerald-500/10 hover:border-emerald-500/30 hover:shadow-md'}
        `}
      >
        <div className="flex items-center gap-4 flex-1">
          <div className="w-10 h-10 bg-emerald-600 rounded-xl flex items-center justify-center text-white shadow-lg shadow-emerald-500/20 flex-shrink-0">
            <Server className="w-5 h-5" />
          </div>
          <div className="flex flex-col">
            <span className="text-[13px] font-black text-slate-900 dark:text-white uppercase tracking-tight">{name}</span>
            <span className="text-[9px] font-bold text-slate-400 uppercase tracking-widest">Active Infrastructure</span>
          </div>
        </div>

        <div className="items-center gap-6 lg:gap-10 mr-8 hidden md:flex">
          <StatColumn label="AUTH" value={authorized} color="text-slate-900 dark:text-white" />
          <StatColumn label="ON" value={online} color="text-emerald-500" />
          <StatColumn label="OFF" value={offline} color="text-rose-500" />
          <StatColumn label="GASP" value={dyingGasp} color="text-blue-500" />
          <StatColumn label="LOSS" value={linkLoss} color="text-rose-400" />
          <StatColumn label="UNK" value={unknown} color="text-purple-500" />
        </div>

        <div className={`transition-transform duration-300 ${isExpanded ? 'rotate-180' : ''}`}>
          <ChevronDown className="w-6 h-6 text-slate-300" />
        </div>
      </div>

      <AnimatePresence>
        {isExpanded && (
          <motion.div 
            initial={{ opacity: 0, y: -20, height: 0 }}
            animate={{ opacity: 1, y: 0, height: 'auto' }}
            exit={{ opacity: 0, y: -20, height: 0 }}
            className="overflow-hidden"
          >
            <div className="p-6 bg-[#F8FAFB] dark:bg-slate-800/20 rounded-[32px] border border-slate-100 dark:border-slate-800 flex flex-col xl:flex-row gap-6">
              <div className="flex-1 bg-white dark:bg-slate-900 p-8 rounded-[28px] shadow-sm border border-slate-50 dark:border-slate-800">
                <h4 className="text-[11px] font-black text-slate-400 uppercase tracking-widest mb-10 flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-rose-500" />
                  OFFLINE TREND ANALYSIS
                </h4>
                <div className="h-[240px] w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={data}>
                      <defs>
                        <linearGradient id={`colorOffline-${seed}`} x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#f43f5e" stopOpacity={0.1}/>
                          <stop offset="95%" stopColor="#f43f5e" stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" strokeOpacity={0.5} />
                      <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fontSize: 10, fontWeight: 700, fill: '#94a3b8' }} dy={10} />
                      <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 10, fontWeight: 700, fill: '#94a3b8' }} />
                      <Tooltip contentStyle={{ backgroundColor: '#1e293b', border: 'none', borderRadius: '16px', fontSize: '11px', color: '#fff', boxShadow: '0 20px 25px -5px rgb(0 0 0 / 0.1)' }} />
                      <Area type="monotone" dataKey="offline" stroke="#f43f5e" strokeWidth={3} fillOpacity={1} fill={`url(#colorOffline-${seed})`} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="flex-1 bg-white dark:bg-slate-900 p-8 rounded-[28px] shadow-sm border border-slate-50 dark:border-slate-800">
                <h4 className="text-[11px] font-black text-slate-400 uppercase tracking-widest mb-10">ALERTS BY CATEGORY</h4>
                <div className="h-[240px] w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={data}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" strokeOpacity={0.5} />
                      <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fontSize: 10, fontWeight: 700, fill: '#94a3b8' }} dy={10} />
                      <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 10, fontWeight: 700, fill: '#94a3b8' }} />
                      <Tooltip contentStyle={{ backgroundColor: '#1e293b', border: 'none', borderRadius: '16px', fontSize: '11px', color: '#fff' }} />
                      <Line type="monotone" dataKey="dyingGasp" stroke="#3b82f6" strokeWidth={3} dot={{ r: 4, strokeWidth: 2, fill: '#fff' }} activeDot={{ r: 6 }} />
                      <Line type="monotone" dataKey="linkLoss" stroke="#fb7185" strokeWidth={3} dot={{ r: 4, strokeWidth: 2, fill: '#fff' }} activeDot={{ r: 6 }} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

const StatColumn = ({ label, value, color }: { label: string, value: number, color: string }) => (
  <div className="flex flex-col items-center min-w-[60px]">
    <span className="text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">{label}</span>
    <span className={`text-[16px] font-black ${color} tabular-nums`}>{value.toLocaleString()}</span>
  </div>
);

export const Dashboard: React.FC = () => {
  const [historyInterval, setHistoryInterval] = useState('6h');
  const [isRefreshing, setIsRefreshing] = useState(false);

  const handleRefresh = () => {
    setIsRefreshing(true);
    setTimeout(() => setIsRefreshing(false), 800);
  };

  const totalStats = {
    authorized: 15420,
    online: 13150,
    offline: 2270,
    dyingGasp: 812,
    linkLoss: 1128,
    unknown: 330
  };

  const oltStats = [
    { name: 'OLT-FH-BSJ-01', authorized: 4200, online: 3600, offline: 600, dyingGasp: 180, linkLoss: 380, unknown: 40, seed: 10 },
    { name: 'OLTZTE-UNA-01', authorized: 4220, online: 3550, offline: 670, dyingGasp: 232, linkLoss: 348, unknown: 90, seed: 15 },
    { name: 'OLT-CORE-03', authorized: 8400, online: 7200, offline: 1200, dyingGasp: 410, linkLoss: 620, unknown: 170, seed: 20 },
    { name: 'OLT-REMOTE-04', authorized: 1240, online: 1100, offline: 140, dyingGasp: 45, linkLoss: 80, unknown: 15, seed: 25 },
    { name: 'OLT-ZTE-TEST', authorized: 2980, online: 2700, offline: 280, dyingGasp: 75, linkLoss: 150, unknown: 55, seed: 30 }
  ];

  const intervals = ['5m', '1h', '6h', '24h', '7d', '30d'];

  return (
    <div className="w-full max-w-7xl mx-auto p-6 lg:p-10 space-y-10 animate-in fade-in slide-in-from-bottom-4 duration-700">
      {/* Header Controls */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-6 pb-8 border-b border-slate-100 dark:border-slate-800">
        <div className="flex flex-col gap-1">
          <h2 className="text-3xl font-black text-slate-900 dark:text-white uppercase tracking-tight">System Overview</h2>
          <p className="text-[12px] font-bold text-slate-400 uppercase tracking-widest">Global Network health monitoring</p>
        </div>

        <div className="flex flex-wrap items-center gap-4">
          <div className="flex bg-slate-100 dark:bg-slate-800 p-1 rounded-xl">
            {intervals.map((int) => (
              <button
                key={int}
                onClick={() => setHistoryInterval(int)}
                className={`px-4 py-2 text-[10px] font-black uppercase rounded-lg transition-all ${
                  historyInterval === int 
                    ? 'bg-white dark:bg-slate-700 text-emerald-600 shadow-sm' 
                    : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-200'
                }`}
              >
                {int}
              </button>
            ))}
          </div>

          <div className="h-8 w-px bg-slate-100 dark:bg-slate-800 mx-2" />

          <button 
            onClick={handleRefresh}
            className={`p-3 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl shadow-lg shadow-emerald-600/20 transition-all ${isRefreshing ? 'rotate-180' : ''}`}
          >
            <RefreshCcw className={`w-4.5 h-4.5 ${isRefreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Main Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-8">
        <StatCard title="Total Authorized" value={totalStats.authorized} icon={Users} color="bg-slate-500" subtext="Units" />
        <StatCard title="Total Online" value={totalStats.online} icon={Wifi} color="bg-emerald-500" subtext="Active" />
        <StatCard title="Total Offline" value={totalStats.offline} icon={WifiOff} color="bg-rose-500" subtext="Inactive" />
      </div>

      {/* Alert panels */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-8">
        <div className="bg-blue-50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-800 p-6 rounded-[24px] flex items-center gap-5 hover:shadow-md transition-all duration-300">
          <div className="w-12 h-12 rounded-xl bg-blue-500 text-white flex items-center justify-center shadow-lg shadow-blue-500/20">
            <Zap className="w-6 h-6" />
          </div>
          <div>
            <h4 className="text-[11px] font-black text-blue-400 uppercase tracking-widest">Dying Gasp</h4>
            <p className="text-2xl font-black text-blue-700 dark:text-blue-300">{totalStats.dyingGasp}</p>
          </div>
        </div>

        <div className="bg-rose-50 dark:bg-rose-900/10 border border-rose-100 dark:border-rose-800 p-6 rounded-[24px] flex items-center gap-5 hover:shadow-md transition-all duration-300">
          <div className="w-12 h-12 rounded-xl bg-rose-400 text-white flex items-center justify-center shadow-lg shadow-rose-400/20">
            <AlertTriangle className="w-6 h-6" />
          </div>
          <div>
            <h4 className="text-[11px] font-black text-rose-400 uppercase tracking-widest">Link Loss</h4>
            <p className="text-2xl font-black text-rose-700 dark:text-rose-300">{totalStats.linkLoss}</p>
          </div>
        </div>

        <div className="bg-purple-50 dark:bg-purple-900/10 border border-purple-100 dark:border-purple-800 p-6 rounded-[24px] flex items-center gap-5 hover:shadow-md transition-all duration-300">
          <div className="w-12 h-12 rounded-xl bg-purple-500 text-white flex items-center justify-center shadow-lg shadow-purple-500/20">
            <HelpCircle className="w-6 h-6" />
          </div>
          <div>
            <h4 className="text-[11px] font-black text-purple-400 uppercase tracking-widest">Unknown</h4>
            <p className="text-2xl font-black text-purple-700 dark:text-purple-300">{totalStats.unknown}</p>
          </div>
        </div>
      </div>

      {/* Per OLT Section - Search and Filter REMOVED from here */}
      <div className="space-y-8 pt-6">
        <h3 className="text-2xl font-black text-slate-800 dark:text-white uppercase tracking-tight">Per OLT Breakdown</h3>
        <div className="flex flex-col gap-6">
          {oltStats.map((olt) => (
            <ExpandableOltRow key={olt.name} {...olt} />
          ))}
        </div>
      </div>
    </div>
  );
};