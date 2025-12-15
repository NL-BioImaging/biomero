# BIOMERO Race Condition Investigation & Fix
*Session Summary - December 11, 2025*

## 🎯 **Problem Statement**

**Race condition in BIOMERO's eventsourcing system** when multiple SLURM clients create workflows simultaneously, causing PostgreSQL unique constraint violations on the `workflowprogress_tracking` table.

### Real Production Error (from logs):
```
2025-12-11 16:21:41,134 ERROR [__main__] [2987] (MainThread) ERROR WITH CONVERTING DATA: 
(psycopg2.errors.UniqueViolation) duplicate key value violates unique constraint "workflowprogress_tracking_pkey"
DETAIL: Key (application_name, notification_id)=(WorkflowTracker, 1177) already exists.
```

### Root Cause:
- **Multiple SLURM clients** (threads 2986 and 2987) running concurrently
- Each client creates its own `WorkflowTracker` instance 
- They share the same **eventsourcing notification tracking table** in PostgreSQL
- Race condition occurs when both try to insert the same `notification_id` simultaneously
- This is an **eventsourcing framework issue**, not our application logic

---

## 🧪 **Investigation Results**

### Key Findings:

1. **The issue is NOT with aggregate collision** - each SLURM client works on different workflows
2. **The collision is in the eventsourcing tracking mechanism** - specifically `workflowprogress_tracking` table
3. **Each client has its own `WorkflowTracker` but shares the same PostgreSQL database**
4. **The race condition happens in milliseconds** (all within `16:21:41` timestamp)

### Technical Context:
- **Eventsourcing Framework**: Uses SQLAlchemy with PostgreSQL
- **Database**: PostgreSQL 16 (BIOMERO uses `database-biomero` container)
- **Connection**: `SQLALCHEMY_URL` environment variable in SlurmClient
- **Tracking Table**: `workflowprogress_tracking` with composite primary key `(application_name, notification_id)`

---

## 🔧 **Solution Approach**

### Strategy: **Retry Mechanism with Exponential Backoff**

The eventsourcing documentation confirms that:
- SQLAlchemy scoped sessions **require explicit `commit()` calls**
- Concurrent access conflicts are expected and should be handled with retries
- This is a common pattern in event-sourced systems

### Implementation Plan:
1. **Add retry wrapper** to `WorkflowTracker.save()` calls
2. **Catch specific PostgreSQL unique constraint violations**
3. **Implement exponential backoff** with jitter
4. **Add transaction rollback** on conflicts
5. **Preserve original exception on max retries exceeded**

---

## 🧪 **Test Infrastructure Created**

### Test Files Available:

1. **`test_race_condition.py`** - Basic SQLite race condition simulation
2. **`test_notification_race.py`** - Thread-based notification tracking test
3. **`test_postgres_race.py`** - **MAIN TEST** - PostgreSQL integration test
4. **`test_notification_table_race.py`** - Alternative PostgreSQL approach

### Key Test: `test_postgres_race.py`
- **Purpose**: Reproduces the exact production scenario
- **Setup**: Uses BIOMERO's PostgreSQL container (`database-biomero`)
- **Scenario**: Multiple SLURM clients creating workflows simultaneously
- **Database**: `postgresql+psycopg2://postgres:biomero-password@localhost:5433/biomero_race_test`
- **Expected**: Should trigger `workflowprogress_tracking` conflicts

### To Run Test:
```bash
# 1. Start PostgreSQL container
cd d:\workspace\NL-BIOMERO
docker-compose up -d database-biomero

# 2. Run race condition test
cd d:\workspace\biomero
.\venvTest\Scripts\python test_postgres_race.py
```

---

## 🔍 **Technical Deep Dive**

### Eventsourcing Architecture:
```
SlurmClient (multiple instances)
    ↓ SQLALCHEMY_URL env var
WorkflowTracker (eventsourcing Application)
    ↓ save() calls
PostgreSQL workflowprogress_tracking table
    ↓ CONFLICT on (application_name, notification_id)
psycopg2.errors.UniqueViolation
```

### Key Code Locations:
- **Database Setup**: `biomero/slurm_client.py` - `initialize_analytics_system()`
- **WorkflowTracker**: `biomero/eventsourcing.py` - `WorkflowTracker` class
- **Save Operations**: Methods like `create_workflow_run()`, `add_task_to_workflow()`, `update_task_status()`
- **Environment**: `SQLALCHEMY_URL` overrides configured database URL

### Current State:
- ✅ **Library files are clean** - no modifications to core BIOMERO code
- ✅ **Test infrastructure ready** - comprehensive race condition reproduction
- ✅ **Problem clearly understood** - eventsourcing notification tracking collision
- ❌ **Fix not yet implemented** - will be done after confirming reproduction

---

## 🚀 **Next Session Action Plan**

### Phase 1: Reproduce (15 minutes)
1. **Start PostgreSQL**: `docker-compose up -d database-biomero` 
2. **Run test**: `.\venvTest\Scripts\python test_postgres_race.py`
3. **Confirm failure**: Should see `UniqueViolation` errors
4. **Analyze results**: Verify we've reproduced the exact production issue

### Phase 2: Implement Fix (30 minutes)  
1. **Add retry mechanism** to `biomero/eventsourcing.py`:
   - `_save_with_retry()` method with exponential backoff
   - Catch `psycopg2.errors.UniqueViolation` specifically
   - Add transaction rollback capability
2. **Modify save calls** in `WorkflowTracker`:
   - `create_workflow_run()` → use `_save_with_retry()`
   - `add_task_to_workflow()` → use `_save_with_retry()`  
   - `update_task_status()` → use `_save_with_retry()`

### Phase 3: Validate Fix (15 minutes)
1. **Re-run test**: Should now show 100% success rate
2. **Check retry logs**: Confirm retry mechanism activates during conflicts  
3. **Run existing tests**: Ensure no regressions in `tests/unit/test_eventsourcing.py`

### Phase 4: Production Deployment
1. **Update biomero package** in NL-BIOMERO deployment
2. **Monitor logs** for race condition resolution
3. **Performance impact assessment**

---

## 📚 **Reference Documentation**

### Eventsourcing Framework:
- **Persistence**: https://eventsourcing.readthedocs.io/en/stable/topics/persistence.html
- **PostgreSQL Config**: Environment variables for connection pooling, timeouts
- **SQLAlchemy Sessions**: Require explicit `commit()` calls for scoped sessions

### BIOMERO Architecture:
- **SlurmClient**: Main entry point, configures `SQLALCHEMY_URL`
- **WorkflowTracker**: Eventsourcing application for workflow management
- **Database Setup**: PostgreSQL 16 in Docker container
- **Multi-client Setup**: Each SLURM process creates its own `WorkflowTracker`

---

## ⚡ **Quick Start Commands**

```bash
# Navigate to workspace
cd d:\workspace\biomero

# Activate environment  
.\venvTest\Scripts\activate

# Start PostgreSQL (from NL-BIOMERO folder)
cd ..\NL-BIOMERO
docker-compose up -d database-biomero
cd ..\biomero

# Run reproduction test
.\venvTest\Scripts\python test_postgres_race.py

# Run existing tests
.\venvTest\Scripts\python -m pytest tests\unit\test_eventsourcing.py -v
```

---

## 🎯 **Success Criteria**

- [ ] **Reproduction confirmed**: Test shows `UniqueViolation` errors
- [ ] **Fix implemented**: Retry mechanism in place with proper error handling  
- [ ] **Test passes**: 100% success rate after fix
- [ ] **No regressions**: All existing tests still pass
- [ ] **Production ready**: Clean, well-tested implementation

**Expected outcome**: Race conditions gracefully handled with automatic retries, no more production errors in BIOMERO logs.