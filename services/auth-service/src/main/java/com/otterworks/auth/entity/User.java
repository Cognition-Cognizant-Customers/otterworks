package com.otterworks.auth.entity;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

@Entity
@Table(name = "users")
@Getter
@Setter
@NoArgsConstructor
public class User {

  @Id
  @GeneratedValue(strategy = GenerationType.UUID)
  private UUID id;

  @Column(nullable = false, unique = true, length = 255)
  private String email;

  @Column(nullable = false, length = 255)
  private String passwordHash;

  @Column(nullable = false, length = 100)
  private String displayName;

  @Column(length = 500)
  private String avatarUrl;

  @Column(nullable = false)
  private boolean emailVerified = false;

  @Column(nullable = false)
  private boolean mfaEnabled = false;

  @Column(length = 255)
  private String mfaSecret;

  @ElementCollection(fetch = FetchType.EAGER)
  @CollectionTable(name = "user_roles", joinColumns = @JoinColumn(name = "user_id"))
  @Column(name = "role")
  @Enumerated(EnumType.STRING)
  private Set<Role> roles = new HashSet<>();

  @Column(nullable = false, updatable = false)
  private Instant createdAt;

  @Column(nullable = false)
  private Instant updatedAt;

  @Column private Instant lastLoginAt;

  @Column(nullable = false)
  private int failedLoginAttempts = 0;

  @Column private Instant lockedUntil;

  @Column(nullable = false)
  private int lockoutCycles = 0;

  @PrePersist
  protected void onCreate() {
    createdAt = Instant.now();
    updatedAt = Instant.now();
  }

  @PreUpdate
  protected void onUpdate() {
    updatedAt = Instant.now();
  }

  public enum Role {
    USER,
    EDITOR,
    ADMIN,
    OWNER
  }
}
