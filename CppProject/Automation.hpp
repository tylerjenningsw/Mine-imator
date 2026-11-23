#pragma once
#include <QString>

namespace CppProject::Automation
{
	void Init();
	bool Active();
	QString WorkDirectory();
	QString LogFile();
	bool ReportFatal(const QString& message);
}
