import { DashboardStats, AnalyticsReport } from '../../models/analytics.model';

export const MOCK_DASHBOARD_STATS: DashboardStats = {
  totalUsers: 1284,
  activeDocuments: 8742,
  storageUsed: '2.4 TB',
  activeSessions: 347,
  usersGrowth: 12.5,
  documentsGrowth: 8.3,
  storageGrowth: 15.2,
  sessionsGrowth: -3.1,
};

export const mockStorage = { usedBytes: 2.4 * 1024 * 1024 * 1024 * 1024 };

export function formatStorageUsed(bytes: number): string {
  const tb = bytes / (1024 ** 4);
  if (tb >= 1) return `${tb.toFixed(1)} TB`;
  const gb = bytes / (1024 ** 3);
  return `${gb.toFixed(1)} GB`;
}

export const MOCK_ANALYTICS_REPORT: AnalyticsReport = {
  userSignups: [
    { label: 'Jan', value: 85 },
    { label: 'Feb', value: 102 },
    { label: 'Mar', value: 130 },
    { label: 'Apr', value: 95 },
    { label: 'May', value: 148 },
    { label: 'Jun', value: 167 },
    { label: 'Jul', value: 142 },
    { label: 'Aug', value: 178 },
    { label: 'Sep', value: 156 },
    { label: 'Oct', value: 198 },
    { label: 'Nov', value: 210 },
    { label: 'Dec', value: 245 },
  ],
  storageUsage: [
    { label: 'Jan', value: 1.2 },
    { label: 'Feb', value: 1.35 },
    { label: 'Mar', value: 1.5 },
    { label: 'Apr', value: 1.62 },
    { label: 'May', value: 1.78 },
    { label: 'Jun', value: 1.9 },
    { label: 'Jul', value: 1.95 },
    { label: 'Aug', value: 2.05 },
    { label: 'Sep', value: 2.15 },
    { label: 'Oct', value: 2.22 },
    { label: 'Nov', value: 2.3 },
    { label: 'Dec', value: 2.4 },
  ],
  documentActivity: [
    { label: 'Mon', value: 1245 },
    { label: 'Tue', value: 1580 },
    { label: 'Wed', value: 1720 },
    { label: 'Thu', value: 1650 },
    { label: 'Fri', value: 1380 },
    { label: 'Sat', value: 420 },
    { label: 'Sun', value: 310 },
  ],
  activeUsers: [
    { label: 'Jan', value: 320 },
    { label: 'Feb', value: 345 },
    { label: 'Mar', value: 390 },
    { label: 'Apr', value: 410 },
    { label: 'May', value: 425 },
    { label: 'Jun', value: 460 },
    { label: 'Jul', value: 448 },
    { label: 'Aug', value: 485 },
    { label: 'Sep', value: 510 },
    { label: 'Oct', value: 535 },
    { label: 'Nov', value: 560 },
    { label: 'Dec', value: 590 },
  ],
  topFileTypes: [
    { label: 'Documents', value: 4250 },
    { label: 'Spreadsheets', value: 1820 },
    { label: 'PDFs', value: 1450 },
    { label: 'Images', value: 890 },
    { label: 'Other', value: 332 },
  ],
  peakHours: [
    { label: '6 AM', value: 45 },
    { label: '7 AM', value: 120 },
    { label: '8 AM', value: 280 },
    { label: '9 AM', value: 450 },
    { label: '10 AM', value: 520 },
    { label: '11 AM', value: 490 },
    { label: '12 PM', value: 380 },
    { label: '1 PM', value: 420 },
    { label: '2 PM', value: 510 },
    { label: '3 PM', value: 480 },
    { label: '4 PM', value: 390 },
    { label: '5 PM', value: 250 },
    { label: '6 PM', value: 120 },
    { label: '7 PM', value: 60 },
  ],
};
