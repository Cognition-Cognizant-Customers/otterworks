package com.otterworks.report.repository;

import com.otterworks.report.model.Report;
import com.otterworks.report.model.ReportCategory;
import com.otterworks.report.model.ReportStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.Date;
import java.util.List;

/**
 * JPA repository for Report entities.
 *
 * LEGACY PATTERNS:
 * - Uses java.util.Date in query parameters (target: java.time.Instant)
 * - JPQL queries with positional-style named params
 * - No use of Specifications or QueryDSL for dynamic filtering
 */
@Repository
public interface ReportRepository extends JpaRepository<Report, Long> {

    List<Report> findByRequestedByOrderByCreatedAtDesc(String requestedBy);

    List<Report> findByRequestedByAndStatusOrderByCreatedAtDesc(
            String requestedBy, ReportStatus status);

    List<Report> findByStatusOrderByCreatedAtAsc(ReportStatus status);

    List<Report> findByCategoryAndStatusOrderByCreatedAtDesc(ReportCategory category, ReportStatus status);

    // LEGACY: java.util.Date parameter types
    @Query("SELECT r FROM Report r WHERE r.createdAt BETWEEN :startDate AND :endDate ORDER BY r.createdAt DESC")
    List<Report> findByDateRange(@Param("startDate") Date startDate, @Param("endDate") Date endDate);

    @Query("SELECT r FROM Report r WHERE r.requestedBy = :userId AND r.category = :category ORDER BY r.createdAt DESC")
    List<Report> findByUserAndCategory(@Param("userId") String userId, @Param("category") ReportCategory category);

    @Query("SELECT COUNT(r) FROM Report r WHERE r.status = :status")
    long countByStatus(@Param("status") ReportStatus status);
}
